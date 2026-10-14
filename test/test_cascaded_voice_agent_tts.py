"""级联语音 TTS 的话轮隔离测试。"""

import asyncio
import unittest

from qll_chat.cascaded_voice_agent.frames import (
    AgentEndFrame,
    AgentTextFrame,
    InterruptFrame,
    OutputAudioFrame,
    StateFrame,
)
from qll_chat.cascaded_voice_agent.pipeline import VoicePipeline
from qll_chat.cascaded_voice_agent.processor import CallbackProcessor
from qll_chat.cascaded_voice_agent.tts import StreamingTTSProcessor
from qll_chat.cascaded_voice_agent.tts_chunker import SentenceChunker


class _FakeTTSService:
    def __init__(self, *, emit_audio: bool = False) -> None:
        self.texts: list[str] = []
        self.finished = asyncio.Event()
        self.cancel_count = 0
        self._emit_audio = emit_audio
        self._on_audio = None

    async def start(self, on_audio, on_error) -> None:
        self._on_audio = on_audio

    async def send_text(self, text: str) -> None:
        self.texts.append(text)
        if self._emit_audio and self._on_audio is not None:
            await self._on_audio(b"\x01\x00\x02\x00")

    async def finish(self) -> None:
        self.finished.set()

    async def cancel(self) -> None:
        self.cancel_count += 1

    async def close(self) -> None:
        return None


class StreamingTTSProcessorTest(unittest.IsolatedAsyncioTestCase):
    """插话后旧话轮不能重新启动 TTS。"""

    async def test_discards_stale_text_and_end_frames_after_interruption(self) -> None:
        service = _FakeTTSService()
        output_frames = []

        async def collect(frame) -> None:
            output_frames.append(frame)

        pipeline = VoicePipeline(
            [
                StreamingTTSProcessor(service, max_chunk_chars=80),
                CallbackProcessor(collect),
            ],
            "tts-stale-turn-test",
        )
        await pipeline.start()
        try:
            await pipeline.queue_frame(InterruptFrame(turn_id=2))
            await pipeline.queue_frame(AgentTextFrame(turn_id=1, text="旧答案。"))
            await pipeline.queue_frame(AgentEndFrame(turn_id=1, answer="旧答案。"))
            await pipeline.queue_frame(AgentTextFrame(turn_id=2, text="新答案。"))
            await pipeline.queue_frame(AgentEndFrame(turn_id=2, answer="新答案。"))
            await asyncio.wait_for(service.finished.wait(), timeout=1)
        finally:
            await pipeline.stop("test-complete")

        self.assertEqual(service.texts, ["新答案。"])
        self.assertFalse(
            any(
                isinstance(frame, AgentEndFrame) and frame.turn_id == 1
                for frame in output_frames
            )
        )

    async def test_first_audio_starts_speaking_and_finish_returns_to_listening(self) -> None:
        service = _FakeTTSService(emit_audio=True)
        output_frames = []

        async def collect(frame) -> None:
            output_frames.append(frame)

        pipeline = VoicePipeline(
            [
                StreamingTTSProcessor(service, max_chunk_chars=80),
                CallbackProcessor(collect),
            ],
            "tts-state-test",
        )
        self.addAsyncCleanup(pipeline.stop, "test-cleanup")
        await pipeline.start()
        await pipeline.queue_frame(AgentTextFrame(turn_id=5, text="你好。"))
        await pipeline.queue_frame(AgentEndFrame(turn_id=5, answer="你好。"))
        await asyncio.wait_for(service.finished.wait(), timeout=1)

        await asyncio.sleep(0)
        visible = [
            (type(frame).__name__, getattr(frame, "event", None))
            for frame in output_frames
            if isinstance(frame, (StateFrame, OutputAudioFrame))
        ]
        self.assertEqual(
            visible,
            [
                ("StateFrame", "assistant.speaking"),
                ("OutputAudioFrame", None),
                ("StateFrame", "assistant.listening"),
            ],
        )
        self.assertEqual(service.texts, ["你好。"])


class SentenceChunkerTest(unittest.TestCase):
    def test_splits_on_chinese_punctuation_and_flushes_tail(self) -> None:
        chunker = SentenceChunker(max_chars=80)

        self.assertEqual(chunker.add("你好，世界"), ("你好，",))
        self.assertEqual(chunker.flush(), "世界")

    def test_maximum_length_prevents_unbounded_buffering(self) -> None:
        chunker = SentenceChunker(max_chars=4)

        self.assertEqual(chunker.add("123456"), ("1234",))
        self.assertEqual(chunker.flush(), "56")

    def test_rejects_invalid_configuration_and_non_text_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须大于 0"):
            SentenceChunker(max_chars=0)
        with self.assertRaisesRegex(TypeError, "必须是字符串"):
            SentenceChunker().add(None)


if __name__ == "__main__":
    unittest.main()
