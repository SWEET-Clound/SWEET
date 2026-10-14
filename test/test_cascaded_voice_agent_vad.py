"""级联语音 Agent 的 PCM 输入和能量 VAD 行为测试。"""

import asyncio
import math
import struct
import unittest

from qll_chat.cascaded_voice_agent.frames import (
    InputAudioFrame,
    InterruptFrame,
    StateFrame,
    UserSpeechStartedFrame,
    UserSpeechStoppedFrame,
)
from qll_chat.cascaded_voice_agent.pipeline import VoicePipeline
from qll_chat.cascaded_voice_agent.processor import CallbackProcessor
from qll_chat.cascaded_voice_agent.vad import EnergyVADProcessor


def _pcm(amplitude: int, duration_ms: int) -> bytes:
    """生成确定性的 440 Hz PCM16，避免测试依赖音频文件。"""
    samples = 16000 * duration_ms // 1000
    return b"".join(
        struct.pack("<h", round(amplitude * math.sin(2 * math.pi * 440 * i / 16000)))
        for i in range(samples)
    )


async def _run_vad(audio_chunks: list[bytes]):
    received = []
    all_audio_forwarded = asyncio.Event()
    audio_count = 0

    async def collect(frame) -> None:
        nonlocal audio_count
        received.append(frame)
        if isinstance(frame, InputAudioFrame):
            audio_count += 1
            if audio_count == len(audio_chunks):
                all_audio_forwarded.set()

    pipeline = VoicePipeline(
        [
            EnergyVADProcessor(threshold=500, start_ms=40, stop_ms=40),
            CallbackProcessor(collect),
        ],
        "vad-test",
    )
    await pipeline.start()
    try:
        for audio in audio_chunks:
            await pipeline.queue_frame(InputAudioFrame(audio=audio))
        await asyncio.wait_for(all_audio_forwarded.wait(), timeout=1)
    finally:
        await pipeline.stop("test-complete")
    return received


class InputAudioFrameTest(unittest.TestCase):
    """音频 Frame 是 WebRTC 传输与算法流水线之间的信任边界。"""

    def test_rejects_audio_outside_first_version_pcm_contract(self) -> None:
        invalid_frames = (
            {"audio": b"\x00\x00", "sample_rate": 8000},
            {"audio": b"\x00\x00", "channels": 2},
            {"audio": b"\x00"},
            {"audio": bytearray(b"\x00\x00")},
        )

        for values in invalid_frames:
            with self.subTest(values=values), self.assertRaises(ValueError):
                InputAudioFrame(**values)


class EnergyVADProcessorTest(unittest.TestCase):
    """通过 VoicePipeline 的公开接口验证 VAD 对外事件。"""

    def test_continuous_silence_does_not_create_a_turn(self) -> None:
        frames = asyncio.run(_run_vad([_pcm(0, 20)] * 10))

        self.assertFalse(any(isinstance(frame, InterruptFrame) for frame in frames))
        self.assertFalse(
            any(isinstance(frame, UserSpeechStartedFrame) for frame in frames)
        )
        self.assertFalse(
            any(isinstance(frame, UserSpeechStoppedFrame) for frame in frames)
        )

    def test_short_noise_does_not_start_a_turn(self) -> None:
        frames = asyncio.run(_run_vad([_pcm(1000, 20), _pcm(0, 20)]))

        self.assertFalse(any(isinstance(frame, InterruptFrame) for frame in frames))
        self.assertFalse(
            any(isinstance(frame, UserSpeechStartedFrame) for frame in frames)
        )

    def test_sustained_speech_starts_and_silence_stops_one_turn(self) -> None:
        audio = [
            _pcm(0, 20),
            _pcm(1000, 20),
            _pcm(1000, 20),
            _pcm(1000, 20),
            _pcm(0, 20),
            _pcm(0, 20),
        ]
        frames = asyncio.run(_run_vad(audio))

        interrupts = [frame for frame in frames if isinstance(frame, InterruptFrame)]
        starts = [
            frame for frame in frames if isinstance(frame, UserSpeechStartedFrame)
        ]
        stops = [
            frame for frame in frames if isinstance(frame, UserSpeechStoppedFrame)
        ]
        states = [
            frame
            for frame in frames
            if isinstance(frame, StateFrame) and frame.event == "assistant.listening"
        ]

        self.assertEqual([frame.turn_id for frame in interrupts], [1])
        self.assertEqual([frame.turn_id for frame in starts], [1])
        self.assertEqual([frame.turn_id for frame in stops], [1])
        self.assertEqual([frame.data["turn_id"] for frame in states], [1])
        self.assertLess(frames.index(interrupts[0]), frames.index(starts[0]))
        self.assertEqual(
            [frame.audio for frame in frames if isinstance(frame, InputAudioFrame)],
            audio,
        )

    def test_each_new_speech_segment_gets_a_new_turn_id(self) -> None:
        one_turn = [
            _pcm(1000, 20),
            _pcm(1000, 20),
            _pcm(0, 20),
            _pcm(0, 20),
        ]
        frames = asyncio.run(_run_vad(one_turn + one_turn))

        starts = [
            frame for frame in frames if isinstance(frame, UserSpeechStartedFrame)
        ]
        stops = [
            frame for frame in frames if isinstance(frame, UserSpeechStoppedFrame)
        ]
        self.assertEqual([frame.turn_id for frame in starts], [1, 2])
        self.assertEqual([frame.turn_id for frame in stops], [1, 2])


if __name__ == "__main__":
    unittest.main()
