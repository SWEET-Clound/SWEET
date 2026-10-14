"""一个级联语音会话的完整生命周期。"""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from contextlib import suppress

from .contracts import MediaSession, TrustedUserContext
from .frames import (
    AgentEndFrame,
    ErrorFrame,
    Frame,
    InputAudioFrame,
    InterruptFrame,
    OutputAudioFrame,
    StateFrame,
    UserSpeechStartedFrame,
)
from .pipeline import VoicePipeline
from .processor import CallbackProcessor, FrameProcessor
from .providers import AliyunStreamingSTTProcessor, CosyVoiceTTSService
from .settings import CascadedVoiceSettings
from .tts import StreamingTTSProcessor
from .vad import EnergyVADProcessor


logger = logging.getLogger(__name__)
ProcessorFactory = Callable[[TrustedUserContext], Sequence[FrameProcessor]]


class CascadedVoiceSession:
    """把编排、云 SDK、Agent SSE 和资源释放封装在一个深模块中。"""

    def __init__(
        self,
        settings: CascadedVoiceSettings,
        *,
        processor_factory: ProcessorFactory | None = None,
    ) -> None:
        self.settings = settings
        self._processor_factory = processor_factory
        self._media: MediaSession | None = None
        self._pipeline: VoicePipeline | None = None
        self._state = "new"
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._closed_event = asyncio.Event()
        self._last_activity = 0.0
        self._idle_task: asyncio.Task[None] | None = None

    async def run(self, media: MediaSession, user: TrustedUserContext) -> None:
        """装配并启动第一版 `VAD → ASR → 现有 Agent → TTS`。"""
        user.validate()
        self.settings.validate()
        if media.session_id != user.voice_session_id:
            raise ValueError("媒体会话与可信 voice_session_id 不一致")
        async with self._close_lock:
            if self._state != "new":
                raise RuntimeError("语音会话只能启动一次")
            self._state = "starting"
            self._media = media
        media.bind_audio_input(self._on_audio)
        media.bind_disconnect(self.close)

        try:
            processors = list(self._create_processors(user))
            if not processors:
                raise ValueError("语音会话至少需要一个算法处理器")
            processors.append(CallbackProcessor(self._handle_output))
            pipeline = VoicePipeline(processors, user.voice_session_id)
            self._pipeline = pipeline
            await pipeline.start()

            async with self._close_lock:
                if self._state != "starting":
                    close_task = self._close_task
                else:
                    self._state = "running"
                    self._last_activity = time.monotonic()
                    self._idle_task = asyncio.create_task(
                        self._watch_idle(),
                        name=f"voice:idle:{user.voice_session_id}",
                    )
                    close_task = None
            if close_task is not None:
                await asyncio.shield(close_task)
                raise RuntimeError("语音会话在启动期间已经关闭")

            await media.send_event(
                {"type": "session.ready", "session_id": media.session_id}
            )
            await media.send_event({"type": "assistant.listening"})
        except BaseException:
            await self.close("startup_failed")
            raise

    def _create_processors(
        self,
        user: TrustedUserContext,
    ) -> Sequence[FrameProcessor]:
        """创建算法链；P7 可注入内存处理器，生产默认使用真实适配器。"""
        if self._processor_factory is not None:
            return self._processor_factory(user)
        from .existing_agent import ExistingAgentProcessor, ExistingAgentSSEClient

        agent_client = ExistingAgentSSEClient(
            self.settings.agent_chat_url,
            user,
            timeout_seconds=self.settings.agent_timeout_seconds,
        )
        return [
            EnergyVADProcessor(
                threshold=self.settings.vad_energy_threshold,
                start_ms=self.settings.vad_start_ms,
                stop_ms=self.settings.vad_stop_ms,
            ),
            AliyunStreamingSTTProcessor(
                self.settings.aliyun_api_key,
                self.settings.aliyun_stt_model,
                self.settings.audio_in_sample_rate,
            ),
            ExistingAgentProcessor(agent_client),
            StreamingTTSProcessor(
                CosyVoiceTTSService(
                    self.settings.aliyun_api_key,
                    self.settings.cosyvoice_model,
                    self.settings.cosyvoice_voice,
                ),
                self.settings.tts_chunk_max_chars,
            ),
        ]

    async def close(self, reason: str) -> None:
        """所有调用者等待同一次清理，保证流水线和媒体连接只关闭一次。"""
        async with self._close_lock:
            if self._close_task is None:
                self._state = "closing"
                self._close_task = asyncio.create_task(
                    self._close_resources(reason),
                    name="voice:session-close",
                )
            close_task = self._close_task
        if close_task is asyncio.current_task():
            return
        await asyncio.shield(close_task)

    async def wait_closed(self) -> None:
        """等待会话完成全部资源清理，供服务层移除注册信息。"""
        await self._closed_event.wait()

    async def _close_resources(self, reason: str) -> None:
        """尽最大努力释放每一层资源，单个清理失败不阻断后续清理。"""
        idle_task, self._idle_task = self._idle_task, None
        pipeline, self._pipeline = self._pipeline, None
        media, self._media = self._media, None
        try:
            if idle_task is not None and idle_task is not asyncio.current_task():
                idle_task.cancel()
                with suppress(asyncio.CancelledError):
                    await idle_task
            try:
                if pipeline is not None:
                    await pipeline.stop(reason)
            except Exception:
                logger.exception("关闭语音流水线失败 | reason=%s", reason)
            try:
                if media is not None:
                    await media.send_event(
                        {"type": "session.closed", "reason": reason}
                    )
            except Exception:
                logger.exception("发送语音会话关闭事件失败 | reason=%s", reason)
            try:
                if media is not None:
                    await media.close()
            except Exception:
                logger.exception("关闭语音媒体会话失败 | reason=%s", reason)
        finally:
            async with self._close_lock:
                self._state = "closed"
                self._closed_event.set()

    async def _on_audio(self, audio: bytes) -> None:
        pipeline = self._pipeline
        if pipeline is None or self._state != "running":
            return
        try:
            await pipeline.queue_frame(
                InputAudioFrame(
                    audio=audio,
                    sample_rate=self.settings.audio_in_sample_rate,
                    channels=1,
                )
            )
        except RuntimeError:
            if self._state not in {"closing", "closed"}:
                raise

    async def _handle_output(self, frame: Frame) -> None:
        media = self._media
        if media is None or self._state not in {"starting", "running"}:
            return
        if isinstance(frame, OutputAudioFrame):
            self._last_activity = time.monotonic()
            await media.send_audio(frame.audio)
        elif isinstance(frame, InterruptFrame):
            await media.clear_audio()
            await media.send_event(
                {"type": "assistant.interrupted", "turn_id": frame.turn_id}
            )
        elif isinstance(frame, StateFrame):
            await media.send_event({"type": frame.event, **dict(frame.data)})
        elif isinstance(frame, UserSpeechStartedFrame):
            self._last_activity = time.monotonic()
        elif isinstance(frame, AgentEndFrame):
            self._last_activity = time.monotonic()
            await media.send_event(
                {
                    "type": "assistant.answer.completed",
                    "turn_id": frame.turn_id,
                    "references": list(frame.references),
                }
            )
        elif isinstance(frame, ErrorFrame):
            event = {
                "type": "session.error",
                "component": frame.component,
                "message": frame.message,
                "fatal": frame.fatal,
            }
            if frame.turn_id is not None:
                event["turn_id"] = frame.turn_id
            await media.send_event(event)
            if frame.fatal:
                asyncio.create_task(self.close(f"fatal:{frame.component}"))

    async def _watch_idle(self) -> None:
        """长时间没有说话或输出时主动释放常驻云连接。"""
        interval = min(5.0, self.settings.session_idle_seconds)
        while self._state == "running":
            await asyncio.sleep(interval)
            if (
                time.monotonic() - self._last_activity
                >= self.settings.session_idle_seconds
            ):
                await self.close("idle_timeout")
                return
