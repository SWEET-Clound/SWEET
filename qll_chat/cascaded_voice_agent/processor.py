"""参考 Pipecat FrameProcessor 实现的最小异步处理器。"""

import asyncio
import itertools
import logging
from contextlib import suppress
from collections.abc import Awaitable, Callable

from .frames import ErrorFrame, Frame, StartFrame, StopFrame, SystemFrame

logger = logging.getLogger(__name__)

FrameCallback = Callable[[Frame], Awaitable[None]]
FatalErrorHandler = Callable[[ErrorFrame], None]


class FrameProcessor:
    """每个处理器拥有独立队列，保证同优先级 Frame 的到达顺序。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._next: FrameProcessor | None = None
        self._queue: asyncio.PriorityQueue[tuple[int, int, Frame]] = asyncio.PriorityQueue()
        self._counter = itertools.count()
        self._task: asyncio.Task[None] | None = None
        self._fatal_error_handler: FatalErrorHandler | None = None

    def link(self, next_processor: "FrameProcessor") -> "FrameProcessor":
        self._next = next_processor
        return next_processor

    def set_fatal_error_handler(self, handler: FatalErrorHandler) -> None:
        """由 Pipeline 注册统一致命错误处理入口。"""
        self._fatal_error_handler = handler

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError(f"处理器已经启动：{self.name}")
        self._task = asyncio.create_task(self._run(), name=f"voice:{self.name}")

    async def wait_closed(self) -> None:
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task

    async def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        await self.wait_closed()

    async def queue_frame(self, frame: Frame) -> None:
        if self._task is None:
            raise RuntimeError(f"处理器尚未启动：{self.name}")
        if self._task.done():
            raise RuntimeError(f"处理器已经关闭：{self.name}")
        priority = 0 if isinstance(frame, SystemFrame) else 10
        await self._queue.put((priority, next(self._counter), frame))

    async def push_frame(self, frame: Frame) -> None:
        if self._next is not None:
            await self._next.queue_frame(frame)

    async def process_frame(self, frame: Frame) -> None:
        """子类只处理自己关心的 Frame，其余 Frame 应继续下传。"""
        await self.push_frame(frame)

    async def on_start(self, frame: StartFrame) -> None:
        """处理器启动钩子。"""

    async def on_stop(self) -> None:
        """处理器清理钩子，只在处理任务退出时调用一次。"""

    async def _run(self) -> None:
        try:
            while True:
                _, _, frame = await self._queue.get()
                try:
                    if isinstance(frame, StartFrame):
                        await self.on_start(frame)
                    await self.process_frame(frame)
                    if isinstance(frame, StopFrame):
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("语音处理器执行失败：%s", self.name)
                    error = ErrorFrame(
                        component=self.name,
                        message=str(exc),
                        fatal=True,
                        turn_id=getattr(frame, "turn_id", None),
                    )
                    try:
                        await self.push_frame(error)
                    except Exception:
                        logger.exception("语音处理器错误事件下传失败：%s", self.name)
                    self._report_fatal_error(error)
                    await self._forward_control_until_stop()
                    return
                finally:
                    self._queue.task_done()
        finally:
            try:
                await self.on_stop()
            except Exception as exc:
                logger.exception("语音处理器清理失败：%s", self.name)
                self._report_fatal_error(
                    ErrorFrame(component=self.name, message=str(exc), fatal=True)
                )

    def _report_fatal_error(self, error: ErrorFrame) -> None:
        if self._fatal_error_handler is not None:
            self._fatal_error_handler(error)

    async def _forward_control_until_stop(self) -> None:
        """故障节点不再处理数据，但仍保证 Stop 能传播到整条流水线。"""
        while True:
            _, _, frame = await self._queue.get()
            try:
                if isinstance(frame, SystemFrame):
                    await self.push_frame(frame)
                if isinstance(frame, StopFrame):
                    return
            finally:
                self._queue.task_done()


class CallbackProcessor(FrameProcessor):
    """把末端 Frame 交还给 WebRTC 媒体会话。"""

    def __init__(self, callback: FrameCallback) -> None:
        super().__init__("output")
        self._callback = callback

    async def process_frame(self, frame: Frame) -> None:
        await self._callback(frame)
