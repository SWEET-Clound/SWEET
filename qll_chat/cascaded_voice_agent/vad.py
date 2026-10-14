"""第一版服务端能量 VAD 处理器。"""

import audioop

from .frames import (
    Frame,
    InputAudioFrame,
    InterruptFrame,
    StateFrame,
    UserSpeechStartedFrame,
    UserSpeechStoppedFrame,
)
from .processor import FrameProcessor


class EnergyVADProcessor(FrameProcessor):
    """用 PCM 能量和连续时长判断说话开始、结束。"""

    def __init__(self, *, threshold: int, start_ms: int, stop_ms: int) -> None:
        super().__init__("vad")
        if threshold <= 0 or start_ms <= 0 or stop_ms <= 0:
            raise ValueError("VAD 阈值和时长必须大于 0")
        self._threshold = threshold
        self._start_ms = start_ms
        self._stop_ms = stop_ms
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._speaking = False
        self._turn_id = 0

    async def process_frame(self, frame: Frame) -> None:
        if not isinstance(frame, InputAudioFrame):
            await self.push_frame(frame)
            return

        duration_ms = len(frame.audio) * 1000 / (
            frame.sample_rate * frame.channels * 2
        )
        energy = audioop.rms(frame.audio, 2) if frame.audio else 0

        if energy >= self._threshold:
            self._speech_ms += duration_ms
            self._silence_ms = 0.0
            if not self._speaking and self._speech_ms >= self._start_ms:
                self._speaking = True
                self._turn_id += 1
                await self.push_frame(InterruptFrame(turn_id=self._turn_id))
                await self.push_frame(UserSpeechStartedFrame(turn_id=self._turn_id))
                await self.push_frame(
                    StateFrame(event="assistant.listening", data={"turn_id": self._turn_id})
                )
        else:
            self._speech_ms = 0.0
            if self._speaking:
                self._silence_ms += duration_ms
                if self._silence_ms >= self._stop_ms:
                    self._speaking = False
                    self._silence_ms = 0.0
                    await self.push_frame(UserSpeechStoppedFrame(turn_id=self._turn_id))

        # ASR 使用连续音频流，因此静音帧也必须继续传递。
        await self.push_frame(frame)
