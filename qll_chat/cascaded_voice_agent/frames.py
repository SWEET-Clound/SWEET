"""自研语音流水线内部传递的 Frame 定义。"""

from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Mapping


@dataclass(slots=True, kw_only=True)
class Frame:
    """所有流水线消息的基类。"""

    created_at: float = field(default_factory=monotonic)


class SystemFrame(Frame):
    """需要优先于普通数据处理的控制消息。"""


@dataclass(slots=True, kw_only=True)
class StartFrame(SystemFrame):
    session_id: str


@dataclass(slots=True, kw_only=True)
class StopFrame(SystemFrame):
    reason: str


@dataclass(slots=True, kw_only=True)
class InterruptFrame(SystemFrame):
    turn_id: int


@dataclass(slots=True, kw_only=True)
class InputAudioFrame(Frame):
    audio: bytes
    sample_rate: int = 16000
    channels: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.audio, bytes):
            raise ValueError("音频数据必须是 bytes")
        if self.sample_rate != 16000 or self.channels != 1:
            raise ValueError("第一版输入固定为 16 kHz 单声道 PCM16")
        if len(self.audio) % 2:
            raise ValueError("PCM16 音频必须包含完整的 2 字节采样")


@dataclass(slots=True, kw_only=True)
class UserSpeechStartedFrame(Frame):
    turn_id: int


@dataclass(slots=True, kw_only=True)
class UserSpeechStoppedFrame(Frame):
    turn_id: int


@dataclass(slots=True, kw_only=True)
class TranscriptFrame(Frame):
    turn_id: int
    text: str
    is_final: bool


@dataclass(slots=True, kw_only=True)
class AgentTextFrame(Frame):
    turn_id: int
    text: str


@dataclass(slots=True, kw_only=True)
class AgentEndFrame(Frame):
    turn_id: int
    answer: str
    references: tuple[Mapping[str, Any], ...] = ()


@dataclass(slots=True, kw_only=True)
class OutputAudioFrame(Frame):
    turn_id: int
    audio: bytes
    sample_rate: int = 24000
    channels: int = 1


@dataclass(slots=True, kw_only=True)
class StateFrame(Frame):
    event: str
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ErrorFrame(SystemFrame):
    component: str
    message: str
    fatal: bool = False
    turn_id: int | None = None
