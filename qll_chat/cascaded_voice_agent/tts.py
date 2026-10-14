"""Agent 文本流到 CosyVoice 音频流的处理器。"""

import asyncio

from .frames import (
    AgentEndFrame,
    AgentTextFrame,
    ErrorFrame,
    InterruptFrame,
    OutputAudioFrame,
    StateFrame,
)
from .processor import FrameProcessor
from .providers.cosyvoice_tts import CosyVoiceTTSService
from .tts_chunker import SentenceChunker


class StreamingTTSProcessor(FrameProcessor):
    """独立任务执行 TTS，使控制队列仍可立即处理用户插话。"""

    def __init__(self, service: CosyVoiceTTSService, max_chunk_chars: int) -> None:
        super().__init__("cosyvoice-tts")
        self._service = service
        self._max_chunk_chars = max_chunk_chars
        self._turn_id: int | None = None
        self._minimum_turn_id = 0
        self._text_queue: asyncio.Queue[str | None] | None = None
        self._tts_task: asyncio.Task[None] | None = None

    async def process_frame(self, frame) -> None:
        if isinstance(frame, InterruptFrame):
            self._minimum_turn_id = max(self._minimum_turn_id, frame.turn_id)
            # 先让媒体层清空播放缓冲，再等待供应商确认释放连接。
            await self.push_frame(frame)
            await self._cancel_turn()
            return
        if isinstance(frame, AgentTextFrame):
            if frame.turn_id < self._minimum_turn_id or not frame.text:
                return
            await self._ensure_turn(frame.turn_id)
            assert self._text_queue is not None
            await self._text_queue.put(frame.text)
            return
        if isinstance(frame, AgentEndFrame):
            if frame.turn_id < self._minimum_turn_id:
                return
            if self._text_queue is not None and frame.turn_id == self._turn_id:
                await self._text_queue.put(None)
            await self.push_frame(frame)
            return
        await self.push_frame(frame)

    async def on_stop(self) -> None:
        await self._cancel_turn()
        await self._service.close()

    async def _ensure_turn(self, turn_id: int) -> None:
        if self._tts_task is not None and not self._tts_task.done() and turn_id == self._turn_id:
            return
        await self._cancel_turn()
        self._turn_id = turn_id
        self._text_queue = asyncio.Queue()
        self._tts_task = asyncio.create_task(
            self._run_turn(turn_id, self._text_queue),
            name=f"voice:tts:{turn_id}",
        )

    async def _run_turn(self, turn_id: int, text_queue: asyncio.Queue[str | None]) -> None:
        chunker = SentenceChunker(self._max_chunk_chars)
        audio_started = False
        error_reported = False

        async def on_audio(audio: bytes) -> None:
            nonlocal audio_started
            if turn_id != self._turn_id or not audio:
                return
            if not audio_started:
                audio_started = True
                await self.push_frame(
                    StateFrame(event="assistant.speaking", data={"turn_id": turn_id})
                )
            await self.push_frame(OutputAudioFrame(turn_id=turn_id, audio=audio))

        async def on_error(message: str) -> None:
            nonlocal error_reported
            if turn_id == self._turn_id and not error_reported:
                error_reported = True
                await self.push_frame(
                    ErrorFrame(
                        component="cosyvoice-tts",
                        message=message,
                        fatal=False,
                        turn_id=turn_id,
                    )
                )

        try:
            await self._service.start(on_audio, on_error)
            while True:
                text = await text_queue.get()
                if text is None:
                    tail = chunker.flush()
                    if tail.strip():
                        await self._service.send_text(tail)
                    await self._service.finish()
                    if turn_id == self._turn_id:
                        await self.push_frame(
                            StateFrame(
                                event="assistant.listening",
                                data={"turn_id": turn_id},
                            )
                        )
                    return
                for chunk in chunker.add(text):
                    if chunk.strip():
                        await self._service.send_text(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if turn_id == self._turn_id and not error_reported:
                error_reported = True
                await self.push_frame(
                    ErrorFrame(
                        component="cosyvoice-tts",
                        message=str(exc),
                        fatal=False,
                        turn_id=turn_id,
                    )
                )
        finally:
            chunker.reset()
            if self._tts_task is asyncio.current_task():
                self._tts_task = None
                self._text_queue = None
                self._turn_id = None

    async def _cancel_turn(self) -> None:
        turn_id = self._turn_id
        task = self._tts_task
        self._turn_id = None
        self._tts_task = None
        self._text_queue = None
        try:
            await self._service.cancel()
        except Exception as exc:
            await self.push_frame(
                ErrorFrame(
                    component="cosyvoice-tts",
                    message=f"取消语音合成失败：{exc}",
                    fatal=False,
                    turn_id=turn_id,
                )
            )
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
