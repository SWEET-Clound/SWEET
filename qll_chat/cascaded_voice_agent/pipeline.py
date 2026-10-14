"""自研级联语音流水线的装配和生命周期。"""

import asyncio
import logging

from .frames import ErrorFrame, Frame, StartFrame, StopFrame
from .processor import FrameProcessor

logger = logging.getLogger(__name__)


class VoicePipeline:
    """一次性线性流水线，统一负责启动、入队、故障和幂等关闭。"""

    _NEW = "new"
    _RUNNING = "running"
    _STOPPING = "stopping"
    _STOPPED = "stopped"

    def __init__(self, processors: list[FrameProcessor], session_id: str) -> None:
        if not processors:
            raise ValueError("流水线至少需要一个处理器")
        self.processors = tuple(processors)
        self.session_id = session_id
        self._state = self._NEW
        self._lifecycle_lock = asyncio.Lock()
        self._stop_task: asyncio.Task[None] | None = None
        self._fatal_error: ErrorFrame | None = None
        for current, following in zip(processors, processors[1:]):
            current.link(following)
        for processor in processors:
            processor.set_fatal_error_handler(self._handle_fatal_error)

    @property
    def fatal_error(self) -> ErrorFrame | None:
        """返回流水线记录到的第一个致命错误。"""
        return self._fatal_error

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._state != self._NEW:
                raise RuntimeError("流水线只能启动一次")
            started: list[FrameProcessor] = []
            try:
                for processor in reversed(self.processors):
                    await processor.start()
                    started.append(processor)
                self._state = self._RUNNING
                await self.processors[0].queue_frame(
                    StartFrame(session_id=self.session_id)
                )
            except Exception:
                await asyncio.gather(
                    *(processor.cancel() for processor in started),
                    return_exceptions=True,
                )
                self._state = self._STOPPED
                raise

    async def queue_frame(self, frame: Frame) -> None:
        async with self._lifecycle_lock:
            if self._state != self._RUNNING:
                raise RuntimeError("流水线当前不接受新的 Frame")
            await self.processors[0].queue_frame(frame)

    async def stop(self, reason: str) -> None:
        async with self._lifecycle_lock:
            if self._state in {self._NEW, self._STOPPED}:
                return
            if self._stop_task is None:
                self._state = self._STOPPING
                self._stop_task = asyncio.create_task(
                    self._stop_processors(reason),
                    name=f"voice:pipeline-stop:{self.session_id}",
                )
            stop_task = self._stop_task
        await stop_task

    async def _stop_processors(self, reason: str) -> None:
        try:
            try:
                await self.processors[0].queue_frame(StopFrame(reason=reason))
            except RuntimeError:
                logger.exception("语音流水线入口已经关闭，强制取消剩余处理器")
                await asyncio.gather(
                    *(processor.cancel() for processor in self.processors),
                    return_exceptions=True,
                )
                return
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(processor.wait_closed() for processor in self.processors),
                    return_exceptions=True,
                ),
                timeout=10,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.error(
                        "语音流水线关闭处理器失败",
                        exc_info=(type(result), result, result.__traceback__),
                    )
        except TimeoutError:
            await asyncio.gather(
                *(processor.cancel() for processor in self.processors),
                return_exceptions=True,
            )
        finally:
            async with self._lifecycle_lock:
                self._state = self._STOPPED

    def _handle_fatal_error(self, error: ErrorFrame) -> None:
        """记录首个致命错误，并异步关闭包括故障节点在内的整条链路。"""
        if self._fatal_error is not None:
            return
        self._fatal_error = error
        if self._state == self._RUNNING and self._stop_task is None:
            asyncio.create_task(
                self.stop(f"fatal:{error.component}"),
                name=f"voice:pipeline-fatal:{self.session_id}",
            )
