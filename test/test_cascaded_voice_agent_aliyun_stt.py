"""阿里云流式 ASR 适配器的生命周期和话轮行为测试。"""

import asyncio
import sys
import threading
import unittest
from types import ModuleType
from unittest.mock import patch

from qll_chat.cascaded_voice_agent.frames import (
    ErrorFrame,
    Frame,
    InputAudioFrame,
    StateFrame,
    TranscriptFrame,
    UserSpeechStartedFrame,
    UserSpeechStoppedFrame,
)
from qll_chat.cascaded_voice_agent.pipeline import VoicePipeline
from qll_chat.cascaded_voice_agent.processor import CallbackProcessor
from qll_chat.cascaded_voice_agent.providers.aliyun_stt import (
    AliyunStreamingSTTProcessor,
)


class _RecognitionResult:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_sentence(self):
        return {"text": self._text, "sentence_end": True}

    def is_sentence_end(self, sentence) -> bool:
        return bool(sentence["sentence_end"])


class _FakeRecognition:
    instances: list["_FakeRecognition"] = []
    final_texts = ["第一轮", "第二轮"]
    fail_send = False

    def __init__(self, *, callback, **kwargs) -> None:
        self.callback = callback
        self.kwargs = kwargs
        self.audio: list[bytes] = []
        self.start_count = 0
        self.stop_count = 0
        self.index = len(self.instances)
        self.instances.append(self)

    def start(self) -> None:
        self.start_count += 1

    def send_audio_frame(self, audio: bytes) -> None:
        if self.fail_send:
            raise RuntimeError("模拟音频发送失败")
        self.audio.append(audio)

    def stop(self) -> None:
        self.stop_count += 1
        text = self.final_texts[min(self.index, len(self.final_texts) - 1)]
        self.callback.on_event(_RecognitionResult(text))


class _BlockingRecognition(_FakeRecognition):
    stop_entered = threading.Event()
    allow_stop = threading.Event()

    def stop(self) -> None:
        self.stop_count += 1
        self.stop_entered.set()
        self.allow_stop.wait(timeout=2)
        self.callback.on_event(_RecognitionResult("取消中的话轮"))


def _fake_dashscope_modules(recognition_type):
    dashscope = ModuleType("dashscope")
    dashscope.api_key = None
    audio = ModuleType("dashscope.audio")
    asr = ModuleType("dashscope.audio.asr")
    asr.Recognition = recognition_type
    dashscope.audio = audio
    audio.asr = asr
    return {
        "dashscope": dashscope,
        "dashscope.audio": audio,
        "dashscope.audio.asr": asr,
    }


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=timeout)


class AliyunStreamingSTTProcessorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _FakeRecognition.instances = []
        _FakeRecognition.fail_send = False
        _BlockingRecognition.instances = []
        _BlockingRecognition.stop_entered.clear()
        _BlockingRecognition.allow_stop.clear()

    async def test_two_turns_each_emit_one_final_and_release_recognition(self) -> None:
        output: list[Frame] = []

        async def collect(frame: Frame) -> None:
            output.append(frame)

        with patch.dict(sys.modules, _fake_dashscope_modules(_FakeRecognition)):
            pipeline = VoicePipeline(
                [
                    AliyunStreamingSTTProcessor("test-key", "paraformer-realtime-v2"),
                    CallbackProcessor(collect),
                ],
                session_id="asr-two-turns",
            )
            self.addAsyncCleanup(pipeline.stop, "test-cleanup")
            await pipeline.start()

            await pipeline.queue_frame(InputAudioFrame(audio=b"\x01\x00" * 800))
            await pipeline.queue_frame(StateFrame(event="test.pre-roll-buffered"))
            await _wait_until(
                lambda: any(
                    isinstance(frame, StateFrame)
                    and frame.event == "test.pre-roll-buffered"
                    for frame in output
                )
            )
            await pipeline.queue_frame(UserSpeechStartedFrame(turn_id=1))
            await _wait_until(lambda: len(_FakeRecognition.instances) == 1)
            await pipeline.queue_frame(InputAudioFrame(audio=b"\x02\x00" * 3200))
            await _wait_until(
                lambda: sum(map(len, _FakeRecognition.instances[0].audio)) >= 6400
            )
            await pipeline.queue_frame(UserSpeechStoppedFrame(turn_id=1))
            await _wait_until(
                lambda: any(
                    isinstance(frame, TranscriptFrame)
                    and frame.turn_id == 1
                    and frame.is_final
                    for frame in output
                )
            )

            await pipeline.queue_frame(UserSpeechStartedFrame(turn_id=2))
            await _wait_until(lambda: len(_FakeRecognition.instances) == 2)
            await pipeline.queue_frame(InputAudioFrame(audio=b"\x03\x00" * 1600))
            await _wait_until(lambda: bool(_FakeRecognition.instances[1].audio))
            await pipeline.queue_frame(UserSpeechStoppedFrame(turn_id=2))
            await _wait_until(
                lambda: len(
                    [
                        frame
                        for frame in output
                        if isinstance(frame, TranscriptFrame) and frame.is_final
                    ]
                )
                == 2
            )

        finals = [
            frame
            for frame in output
            if isinstance(frame, TranscriptFrame) and frame.is_final
        ]
        self.assertEqual(
            [(frame.turn_id, frame.text) for frame in finals],
            [(1, "第一轮"), (2, "第二轮")],
        )
        self.assertEqual(
            [instance.stop_count for instance in _FakeRecognition.instances], [1, 1]
        )
        self.assertTrue(
            all(
                len(chunk) <= 3200
                for instance in _FakeRecognition.instances
                for chunk in instance.audio
            )
        )
        self.assertEqual(sum(map(len, _FakeRecognition.instances[0].audio)), 8000)

    async def test_tail_send_failure_releases_sdk_without_truncated_final(self) -> None:
        output: list[Frame] = []

        async def collect(frame: Frame) -> None:
            output.append(frame)

        _FakeRecognition.fail_send = True
        with patch.dict(sys.modules, _fake_dashscope_modules(_FakeRecognition)):
            pipeline = VoicePipeline(
                [
                    AliyunStreamingSTTProcessor("test-key", "paraformer-realtime-v2"),
                    CallbackProcessor(collect),
                ],
                session_id="asr-send-error",
            )
            self.addAsyncCleanup(pipeline.stop, "test-cleanup")
            await pipeline.start()
            await pipeline.queue_frame(UserSpeechStartedFrame(turn_id=3))
            await _wait_until(lambda: len(_FakeRecognition.instances) == 1)
            await pipeline.queue_frame(InputAudioFrame(audio=b"\x04\x00" * 800))
            await pipeline.queue_frame(StateFrame(event="test.tail-buffered"))
            await _wait_until(
                lambda: any(
                    isinstance(frame, StateFrame)
                    and frame.event == "test.tail-buffered"
                    for frame in output
                )
            )
            await pipeline.queue_frame(UserSpeechStoppedFrame(turn_id=3))
            await _wait_until(
                lambda: any(isinstance(frame, ErrorFrame) for frame in output)
            )

        errors = [frame for frame in output if isinstance(frame, ErrorFrame)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].component, "aliyun-asr")
        self.assertEqual(errors[0].turn_id, 3)
        self.assertEqual(_FakeRecognition.instances[0].stop_count, 1)
        self.assertFalse(
            any(
                isinstance(frame, TranscriptFrame) and frame.is_final
                for frame in output
            )
        )

    async def test_cancel_waits_for_blocking_sdk_stop(self) -> None:
        output: list[Frame] = []

        async def collect(frame: Frame) -> None:
            output.append(frame)

        with patch.dict(sys.modules, _fake_dashscope_modules(_BlockingRecognition)):
            processor = AliyunStreamingSTTProcessor(
                "test-key", "paraformer-realtime-v2"
            )
            collector = CallbackProcessor(collect)
            processor.link(collector)
            await collector.start()
            await processor.start()
            self.addAsyncCleanup(collector.cancel)
            self.addAsyncCleanup(processor.cancel)

            from qll_chat.cascaded_voice_agent.frames import StartFrame, StopFrame

            await processor.queue_frame(StartFrame(session_id="asr-cancel"))
            await processor.queue_frame(UserSpeechStartedFrame(turn_id=4))
            await _wait_until(lambda: len(_BlockingRecognition.instances) == 1)
            await processor.queue_frame(UserSpeechStoppedFrame(turn_id=4))
            await asyncio.to_thread(_BlockingRecognition.stop_entered.wait, 1)

            cancel_task = asyncio.create_task(processor.cancel())
            await asyncio.sleep(0)
            self.assertFalse(cancel_task.done())
            _BlockingRecognition.allow_stop.set()
            await asyncio.wait_for(cancel_task, timeout=1)
            await collector.queue_frame(StopFrame(reason="test-complete"))
            await collector.wait_closed()

        self.assertEqual(_BlockingRecognition.instances[0].stop_count, 1)
        self.assertFalse(
            any(
                isinstance(frame, TranscriptFrame) and frame.is_final
                for frame in output
            )
        )


if __name__ == "__main__":
    unittest.main()
