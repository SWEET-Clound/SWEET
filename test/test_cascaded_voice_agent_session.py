"""无 WebRTC 级联会话的本地集成与资源释放测试。"""

import asyncio
import unittest

from qll_chat.cascaded_voice_agent.contracts import TrustedUserContext
from qll_chat.cascaded_voice_agent.frames import (
    AgentEndFrame,
    AgentTextFrame,
    Frame,
    TranscriptFrame,
    UserSpeechStoppedFrame,
)
from qll_chat.cascaded_voice_agent.processor import FrameProcessor
from qll_chat.cascaded_voice_agent.session import CascadedVoiceSession
from qll_chat.cascaded_voice_agent.settings import CascadedVoiceSettings
from qll_chat.cascaded_voice_agent.tts import StreamingTTSProcessor
from qll_chat.cascaded_voice_agent.vad import EnergyVADProcessor


class _MemoryMedia:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.events: list[dict] = []
        self.audio: list[bytes] = []
        self.clear_count = 0
        self.close_count = 0
        self._audio_handler = None
        self._disconnect_handler = None

    def bind_audio_input(self, handler) -> None:
        self._audio_handler = handler

    def bind_disconnect(self, handler) -> None:
        self._disconnect_handler = handler

    async def receive_audio(self, audio: bytes) -> None:
        assert self._audio_handler is not None
        await self._audio_handler(audio)

    async def send_audio(self, audio: bytes) -> None:
        self.audio.append(audio)

    async def clear_audio(self) -> None:
        self.clear_count += 1

    async def send_event(self, event) -> None:
        self.events.append(dict(event))

    async def close(self) -> None:
        self.close_count += 1


class _MemoryASRProcessor(FrameProcessor):
    def __init__(self) -> None:
        super().__init__("memory-asr")

    async def process_frame(self, frame: Frame) -> None:
        if isinstance(frame, UserSpeechStoppedFrame):
            await self.push_frame(
                TranscriptFrame(
                    turn_id=frame.turn_id,
                    text=f"第{frame.turn_id}轮问题",
                    is_final=True,
                )
            )
        await self.push_frame(frame)


class _MemoryAgentProcessor(FrameProcessor):
    def __init__(self) -> None:
        super().__init__("memory-agent")

    async def process_frame(self, frame: Frame) -> None:
        if isinstance(frame, TranscriptFrame) and frame.is_final:
            answer = f"第{frame.turn_id}轮回答。"
            await self.push_frame(
                AgentTextFrame(turn_id=frame.turn_id, text=answer)
            )
            await self.push_frame(
                AgentEndFrame(turn_id=frame.turn_id, answer=answer)
            )
        await self.push_frame(frame)


class _MemoryTTSService:
    def __init__(self) -> None:
        self._on_audio = None
        self.close_count = 0

    async def start(self, on_audio, on_error) -> None:
        self._on_audio = on_audio

    async def send_text(self, text: str) -> None:
        assert self._on_audio is not None
        await self._on_audio(b"\x01\x00\x02\x00")

    async def finish(self) -> None:
        return None

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        self.close_count += 1


def _settings(*, idle_seconds: float = 1.0) -> CascadedVoiceSettings:
    return CascadedVoiceSettings(
        aliyun_api_key="test-key",
        aliyun_stt_model="paraformer-realtime-v2",
        cosyvoice_model="cosyvoice-v2",
        cosyvoice_voice="longxiaochun",
        agent_chat_url="http://127.0.0.1:8000/chat",
        internal_service_token="test-token",
        session_idle_seconds=idle_seconds,
        vad_energy_threshold=100,
        vad_start_ms=20,
        vad_stop_ms=20,
    )


def _trusted_user(session_id: str) -> TrustedUserContext:
    return TrustedUserContext(
        tenant_id="tenant-test",
        user_id="user-test",
        voice_session_id=session_id,
        agent_request_template={
            "channel": "test",
            "functionName": "voiceAgent",
            "algExtra": {},
            "tenantId": "tenant-test",
            "userId": "user-test",
        },
    )


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=timeout)


class CascadedVoiceSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_completes_ten_turns_and_closes_resources_once(self) -> None:
        media = _MemoryMedia("session-ten-turns")
        tts_service = _MemoryTTSService()

        def processors(user):
            return [
                EnergyVADProcessor(threshold=100, start_ms=20, stop_ms=20),
                _MemoryASRProcessor(),
                _MemoryAgentProcessor(),
                StreamingTTSProcessor(tts_service, max_chunk_chars=80),
            ]

        session = CascadedVoiceSession(_settings(), processor_factory=processors)
        await session.run(media, _trusted_user(media.session_id))

        speech = (1000).to_bytes(2, "little", signed=True) * 320
        silence = b"\x00\x00" * 320
        for turn_id in range(1, 11):
            await media.receive_audio(speech)
            await media.receive_audio(silence)
            await _wait_until(
                lambda: any(
                    event.get("type") == "assistant.listening"
                    and event.get("turn_id") == turn_id
                    for event in media.events
                )
            )

        await asyncio.gather(
            session.close("first"),
            session.close("second"),
            session.close("third"),
        )
        await session.wait_closed()
        await asyncio.sleep(0)

        completed_turns = [
            event["turn_id"]
            for event in media.events
            if event.get("type") == "assistant.answer.completed"
        ]
        self.assertEqual(completed_turns, list(range(1, 11)))
        self.assertEqual(len(media.audio), 10)
        self.assertEqual(media.clear_count, 10)
        self.assertEqual(media.close_count, 1)
        self.assertEqual(tts_service.close_count, 1)
        self.assertFalse(
            [
                task.get_name()
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
                and not task.done()
                and task.get_name().startswith("voice:")
            ]
        )

    async def test_idle_timeout_closes_session(self) -> None:
        media = _MemoryMedia("session-idle")
        tts_service = _MemoryTTSService()

        def processors(user):
            return [StreamingTTSProcessor(tts_service, max_chunk_chars=80)]

        session = CascadedVoiceSession(
            _settings(idle_seconds=0.02), processor_factory=processors
        )
        await session.run(media, _trusted_user(media.session_id))
        await asyncio.wait_for(session.wait_closed(), timeout=1)

        self.assertEqual(media.close_count, 1)
        self.assertEqual(tts_service.close_count, 1)
        self.assertIn(
            {"type": "session.closed", "reason": "idle_timeout"}, media.events
        )


if __name__ == "__main__":
    unittest.main()
