"""级联语音编排内核的公开行为测试。"""

import asyncio
import unittest

from qll_chat.cascaded_voice_agent.frames import (
    AgentTextFrame,
    ErrorFrame,
    Frame,
    InterruptFrame,
    StartFrame,
    StopFrame,
)
from qll_chat.cascaded_voice_agent.pipeline import VoicePipeline
from qll_chat.cascaded_voice_agent.processor import CallbackProcessor, FrameProcessor


class _BlockingRecorder(FrameProcessor):
    def __init__(self) -> None:
        super().__init__("recorder")
        self.frames: list[Frame] = []
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.received = asyncio.Event()

    async def process_frame(self, frame: Frame) -> None:
        if isinstance(frame, AgentTextFrame) and frame.text == "block":
            self.frames.append(frame)
            self.blocked.set()
            await self.release.wait()
        elif not isinstance(frame, (StartFrame, StopFrame)):
            self.frames.append(frame)
        if len(self.frames) == 4:
            self.received.set()


class _CountingProcessor(FrameProcessor):
    def __init__(self) -> None:
        super().__init__("counting")
        self.stop_count = 0

    async def on_stop(self) -> None:
        self.stop_count += 1


class _FailingProcessor(FrameProcessor):
    async def process_frame(self, frame: Frame) -> None:
        if isinstance(frame, AgentTextFrame):
            raise ValueError("模拟处理失败")
        await self.push_frame(frame)


class VoicePipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_control_frame_overtakes_queued_data_frames(self) -> None:
        processor = _BlockingRecorder()
        pipeline = VoicePipeline([processor], session_id="session-priority")
        self.addAsyncCleanup(pipeline.stop, "test-cleanup")
        await pipeline.start()

        await pipeline.queue_frame(AgentTextFrame(turn_id=1, text="block"))
        await asyncio.wait_for(processor.blocked.wait(), timeout=1)
        await pipeline.queue_frame(AgentTextFrame(turn_id=1, text="first"))
        await pipeline.queue_frame(AgentTextFrame(turn_id=1, text="second"))
        await pipeline.queue_frame(InterruptFrame(turn_id=2))
        processor.release.set()

        await asyncio.wait_for(processor.received.wait(), timeout=1)
        self.assertEqual(
            [
                (type(frame).__name__, getattr(frame, "text", None))
                for frame in processor.frames
            ],
            [
                ("AgentTextFrame", "block"),
                ("InterruptFrame", None),
                ("AgentTextFrame", "first"),
                ("AgentTextFrame", "second"),
            ],
        )
        await pipeline.stop("test-complete")

    async def test_stop_is_idempotent_and_pipeline_cannot_be_reused(self) -> None:
        processor = _CountingProcessor()
        pipeline = VoicePipeline([processor], session_id="session-stop")
        self.addAsyncCleanup(pipeline.stop, "test-cleanup")
        await pipeline.start()

        await asyncio.gather(
            pipeline.stop("first"),
            pipeline.stop("second"),
            pipeline.stop("third"),
        )

        self.assertEqual(processor.stop_count, 1)
        with self.assertRaisesRegex(RuntimeError, "不接受新的 Frame"):
            await pipeline.queue_frame(AgentTextFrame(turn_id=1, text="late"))
        with self.assertRaisesRegex(RuntimeError, "只能启动一次"):
            await pipeline.start()

    async def test_processor_failure_is_reported_and_stops_pipeline(self) -> None:
        errors: list[ErrorFrame] = []
        error_received = asyncio.Event()

        async def collect(frame: Frame) -> None:
            if isinstance(frame, ErrorFrame):
                errors.append(frame)
                error_received.set()

        pipeline = VoicePipeline(
            [_FailingProcessor("asr"), CallbackProcessor(collect)],
            session_id="session-error",
        )
        self.addAsyncCleanup(pipeline.stop, "test-cleanup")
        await pipeline.start()
        await pipeline.queue_frame(AgentTextFrame(turn_id=7, text="fail"))

        await asyncio.wait_for(error_received.wait(), timeout=1)
        await pipeline.stop("test-complete")

        self.assertIsNotNone(pipeline.fatal_error)
        assert pipeline.fatal_error is not None
        self.assertEqual(pipeline.fatal_error.component, "asr")
        self.assertEqual(pipeline.fatal_error.turn_id, 7)
        self.assertEqual(pipeline.fatal_error.message, "模拟处理失败")
        self.assertEqual(errors, [pipeline.fatal_error])


if __name__ == "__main__":
    unittest.main()
