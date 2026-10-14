"""CosyVoice SDK 封装的 PCM 对齐、完成与取消测试。"""

import asyncio
import sys
import unittest
from types import ModuleType
from unittest.mock import patch

from qll_chat.cascaded_voice_agent.providers.cosyvoice_tts import CosyVoiceTTSService


class _AudioFormat:
    PCM_24000HZ_MONO_16BIT = "pcm-24000-mono-16bit"


class _FakeSpeechSynthesizer:
    instances: list["_FakeSpeechSynthesizer"] = []
    odd_only = False

    def __init__(self, *, callback, **kwargs) -> None:
        self.callback = callback
        self.kwargs = kwargs
        self.texts: list[str] = []
        self.complete_count = 0
        self.cancel_timeouts: list[int] = []
        self.instances.append(self)

    def streaming_call(self, text: str) -> None:
        self.texts.append(text)
        self.callback.on_data(b"\x01")
        if not self.odd_only:
            self.callback.on_data(b"\x02\x03\x04")

    def streaming_complete(self) -> None:
        self.complete_count += 1
        self.callback.on_complete()

    def streaming_cancel(self, timeout_ms: int) -> None:
        self.cancel_timeouts.append(timeout_ms)


def _fake_dashscope_modules():
    dashscope = ModuleType("dashscope")
    dashscope.api_key = None
    audio = ModuleType("dashscope.audio")
    tts_v2 = ModuleType("dashscope.audio.tts_v2")
    tts_v2.AudioFormat = _AudioFormat
    tts_v2.SpeechSynthesizer = _FakeSpeechSynthesizer
    dashscope.audio = audio
    audio.tts_v2 = tts_v2
    return {
        "dashscope": dashscope,
        "dashscope.audio": audio,
        "dashscope.audio.tts_v2": tts_v2,
    }


class CosyVoiceTTSServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _FakeSpeechSynthesizer.instances = []
        _FakeSpeechSynthesizer.odd_only = False

    async def test_streams_only_complete_pcm16_samples_and_finishes_once(self) -> None:
        audio: list[bytes] = []
        errors: list[str] = []
        service = CosyVoiceTTSService("test-key", "cosyvoice-v2", "longxiaochun")

        async def on_audio(chunk: bytes) -> None:
            audio.append(chunk)

        async def on_error(message: str) -> None:
            errors.append(message)

        with patch.dict(sys.modules, _fake_dashscope_modules()):
            await service.start(on_audio, on_error)
            await service.send_text("你好")
            await service.finish()

        synthesizer = _FakeSpeechSynthesizer.instances[0]
        self.assertEqual(synthesizer.texts, ["你好"])
        self.assertEqual(synthesizer.complete_count, 1)
        self.assertEqual(b"".join(audio), b"\x01\x02\x03\x04")
        self.assertTrue(all(len(chunk) % 2 == 0 for chunk in audio))
        self.assertEqual(errors, [])

    async def test_incomplete_pcm16_sample_is_reported(self) -> None:
        errors: list[str] = []
        service = CosyVoiceTTSService("test-key", "cosyvoice-v2", "longxiaochun")
        _FakeSpeechSynthesizer.odd_only = True

        async def on_audio(chunk: bytes) -> None:
            return None

        async def on_error(message: str) -> None:
            errors.append(message)

        with patch.dict(sys.modules, _fake_dashscope_modules()):
            await service.start(on_audio, on_error)
            await service.send_text("你好")
            await service.finish()

        self.assertEqual(errors, ["CosyVoice 返回了不完整的 PCM16 采样"])

    async def test_cancel_releases_sdk_and_drops_late_audio(self) -> None:
        audio: list[bytes] = []
        service = CosyVoiceTTSService("test-key", "cosyvoice-v2", "longxiaochun")

        async def on_audio(chunk: bytes) -> None:
            audio.append(chunk)

        async def on_error(message: str) -> None:
            return None

        with patch.dict(sys.modules, _fake_dashscope_modules()):
            await service.start(on_audio, on_error)
            synthesizer = _FakeSpeechSynthesizer.instances[0]
            await service.cancel()
            synthesizer.callback.on_data(b"\x01\x00")
            await asyncio.sleep(0)

        self.assertEqual(synthesizer.cancel_timeouts, [10_000])
        self.assertEqual(audio, [])


if __name__ == "__main__":
    unittest.main()
