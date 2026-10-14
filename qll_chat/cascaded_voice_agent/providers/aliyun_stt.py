"""阿里云 Paraformer 实时 ASR 的话轮级 Processor 适配器。"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any

from ..frames import (
    ErrorFrame,
    Frame,
    InputAudioFrame,
    StopFrame,
    TranscriptFrame,
    UserSpeechStartedFrame,
    UserSpeechStoppedFrame,
)
from ..processor import FrameProcessor


class _RecognitionCallback:
    """收集 SDK 回调结果，并把中间转写安全送回事件循环。"""

    def __init__(
        self,
        owner: "AliyunStreamingSTTProcessor",
        turn_id: int,
        generation: int,
    ) -> None:
        self._owner = owner
        self._turn_id = turn_id
        self._generation = generation
        self._lock = threading.Lock()
        self._finalized_parts: list[str] = []
        self._partial_text = ""
        self._error_message = ""

    @property
    def error_message(self) -> str:
        with self._lock:
            return self._error_message

    def final_text(self) -> str:
        with self._lock:
            return "".join([*self._finalized_parts, self._partial_text]).strip()

    def on_open(self) -> None:
        return None

    def on_complete(self) -> None:
        return None

    def on_close(self) -> None:
        return None

    def on_event(self, result: Any) -> None:
        sentence = result.get_sentence()
        if not sentence:
            return
        sentences = sentence if isinstance(sentence, list) else [sentence]
        for item in sentences:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            is_sentence_end = bool(result.is_sentence_end(item))
            with self._lock:
                if is_sentence_end:
                    self._finalized_parts.append(text)
                    self._partial_text = ""
                else:
                    self._partial_text = text
                current_text = "".join(
                    [*self._finalized_parts, self._partial_text]
                ).strip()
            if current_text:
                self._owner.emit_from_callback(
                    TranscriptFrame(
                        turn_id=self._turn_id,
                        text=current_text,
                        is_final=False,
                    ),
                    self._generation,
                )

    def on_error(self, result: Any) -> None:
        message = str(getattr(result, "message", None) or result)
        with self._lock:
            self._error_message = message
        self._owner.emit_from_callback(
            ErrorFrame(
                component="aliyun-asr",
                message=message,
                fatal=False,
                turn_id=self._turn_id,
            ),
            self._generation,
        )


class AliyunStreamingSTTProcessor(FrameProcessor):
    """把每个 VAD 话轮转换成中间转写和唯一最终转写。"""

    _PRE_ROLL_MS = 300
    _SEND_CHUNK_MS = 100

    def __init__(self, api_key: str, model: str, sample_rate: int = 16000) -> None:
        super().__init__("aliyun-asr")
        if not str(api_key or "").strip():
            raise ValueError("阿里云 ASR 缺少 API Key")
        if not str(model or "").strip():
            raise ValueError("阿里云 ASR 缺少模型名称")
        if sample_rate != 16000:
            raise ValueError("第一版 ASR 仅接受 16 kHz 单声道 PCM16")
        self._api_key = api_key
        self._model = model
        self._sample_rate = sample_rate
        self._recognition_factory: Any = None
        self._recognition: Any = None
        self._callback: _RecognitionCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._turn_id: int | None = None
        self._generation = 0
        self._send_buffer = bytearray()
        self._pre_roll: deque[bytes] = deque()
        self._pre_roll_bytes = 0
        self._callback_tasks: set[asyncio.Task[None]] = set()
        self._pre_roll_limit = self._bytes_for_ms(self._PRE_ROLL_MS)
        self._send_chunk_bytes = self._bytes_for_ms(self._SEND_CHUNK_MS)

    async def on_start(self, frame: Frame) -> None:
        import dashscope
        from dashscope.audio.asr import Recognition

        dashscope.api_key = self._api_key
        self._recognition_factory = Recognition
        self._loop = asyncio.get_running_loop()

    async def process_frame(self, frame: Frame) -> None:
        if isinstance(frame, UserSpeechStartedFrame):
            await self._start_turn(frame.turn_id)
        elif isinstance(frame, UserSpeechStoppedFrame):
            if frame.turn_id == self._turn_id:
                await self._finish_turn(emit_final=True)
        elif isinstance(frame, InputAudioFrame):
            self._validate_audio(frame)
            if self._recognition is None:
                self._remember_pre_roll(frame.audio)
            else:
                self._feed_audio(frame.audio)
        elif isinstance(frame, StopFrame):
            await self._finish_turn(emit_final=False)
        await self.push_frame(frame)

    async def on_stop(self) -> None:
        await self._finish_turn(emit_final=False)
        await self._drain_callback_tasks()
        self._loop = None

    async def _start_turn(self, turn_id: int) -> None:
        if self._recognition is not None:
            await self._finish_turn(emit_final=False)
        if self._recognition_factory is None:
            raise RuntimeError("阿里云 ASR 尚未初始化")

        self._generation += 1
        callback = _RecognitionCallback(self, turn_id, self._generation)
        recognition = self._recognition_factory(
            model=self._model,
            callback=callback,
            format="pcm",
            sample_rate=self._sample_rate,
            semantic_punctuation_enabled=False,
        )
        self._recognition = recognition
        self._callback = callback
        self._turn_id = turn_id
        start_task = asyncio.create_task(asyncio.to_thread(recognition.start))
        try:
            await asyncio.shield(start_task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(start_task)
                await asyncio.to_thread(recognition.stop)
            finally:
                self._reset_turn()
            raise
        except BaseException:
            self._reset_turn()
            raise

        # shortcut: 首版固定保留 300ms 前导音频，P9 再按手机实测校准。
        buffered = tuple(self._pre_roll)
        self._clear_pre_roll()
        for audio in buffered:
            self._feed_audio(audio)

    async def _finish_turn(self, *, emit_final: bool) -> None:
        recognition = self._recognition
        callback = self._callback
        turn_id = self._turn_id
        if recognition is None or callback is None or turn_id is None:
            return

        stop_error: Exception | None = None
        cancelled_error: asyncio.CancelledError | None = None
        if self._send_buffer:
            try:
                recognition.send_audio_frame(bytes(self._send_buffer))
            except Exception as exc:
                stop_error = exc
            finally:
                self._send_buffer.clear()

        try:
            stop_task = asyncio.create_task(asyncio.to_thread(recognition.stop))
            try:
                await asyncio.shield(stop_task)
            except asyncio.CancelledError as exc:
                cancelled_error = exc
                await asyncio.shield(stop_task)
        except Exception as exc:
            stop_error = stop_error or exc

        await self._drain_callback_tasks()
        final_text = callback.final_text()
        callback_error = callback.error_message
        self._reset_turn()

        if cancelled_error is not None:
            raise cancelled_error

        if stop_error is not None and not callback_error:
            await self.push_frame(
                ErrorFrame(
                    component="aliyun-asr",
                    message=f"语音识别收尾失败：{stop_error}",
                    fatal=False,
                    turn_id=turn_id,
                )
            )
        if emit_final and final_text and not callback_error and stop_error is None:
            await self.push_frame(
                TranscriptFrame(
                    turn_id=turn_id,
                    text=final_text,
                    is_final=True,
                )
            )

    def _feed_audio(self, audio: bytes) -> None:
        if self._recognition is None or not audio:
            return
        self._send_buffer.extend(audio)
        while len(self._send_buffer) >= self._send_chunk_bytes:
            chunk = bytes(self._send_buffer[: self._send_chunk_bytes])
            del self._send_buffer[: self._send_chunk_bytes]
            self._recognition.send_audio_frame(chunk)

    def _remember_pre_roll(self, audio: bytes) -> None:
        if not audio:
            return
        self._pre_roll.append(audio)
        self._pre_roll_bytes += len(audio)
        while self._pre_roll_bytes > self._pre_roll_limit and self._pre_roll:
            self._pre_roll_bytes -= len(self._pre_roll.popleft())

    def _clear_pre_roll(self) -> None:
        self._pre_roll.clear()
        self._pre_roll_bytes = 0

    def _reset_turn(self) -> None:
        self._recognition = None
        self._callback = None
        self._turn_id = None
        self._send_buffer.clear()
        self._generation += 1

    async def _drain_callback_tasks(self) -> None:
        await asyncio.sleep(0)
        tasks = tuple(self._callback_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def emit_from_callback(self, frame: Frame, generation: int) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(
                self._schedule_callback_frame,
                frame,
                generation,
            )

    def _schedule_callback_frame(self, frame: Frame, generation: int) -> None:
        if generation != self._generation:
            return
        task = asyncio.create_task(self.push_frame(frame))
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)

    def _bytes_for_ms(self, duration_ms: int) -> int:
        return self._sample_rate * 2 * duration_ms // 1000

    def _validate_audio(self, frame: InputAudioFrame) -> None:
        if frame.sample_rate != self._sample_rate or frame.channels != 1:
            raise ValueError("ASR 仅接受 16 kHz 单声道 PCM16")
