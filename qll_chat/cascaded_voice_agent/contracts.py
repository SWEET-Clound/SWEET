"""语音服务信任边界上的稳定契约。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable, Mapping, Protocol


class VoiceSessionState(StrEnum):
    """一条实时语音会话的用户可见状态。"""

    CONNECTING = "connecting"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class TrustedUserContext:
    """业务后端创建的可信身份和现有 Agent 请求模板。"""

    tenant_id: str
    user_id: str
    voice_session_id: str
    agent_request_template: Mapping[str, Any]

    def validate(self) -> None:
        if not self.tenant_id or not self.user_id or not self.voice_session_id:
            raise ValueError("tenant_id、user_id 和 voice_session_id 不能为空")
        if not isinstance(self.agent_request_template, Mapping):
            raise ValueError("agent_request_template 必须是对象")
        required = ("channel", "functionName", "algExtra")
        missing = [name for name in required if name not in self.agent_request_template]
        if missing:
            raise ValueError(f"Agent 请求模板缺少字段：{', '.join(missing)}")
        template_tenant = str(self.agent_request_template.get("tenantId", self.tenant_id))
        template_user = str(self.agent_request_template.get("userId", self.user_id))
        if template_tenant != self.tenant_id or template_user != self.user_id:
            raise ValueError("Agent 请求模板中的身份与可信上下文不一致")


class MediaSession(Protocol):
    """WebRTC 媒体会话接口，编排层不依赖 aiortc 对象。"""

    session_id: str

    def bind_audio_input(self, handler: Callable[[bytes], Awaitable[None]]) -> None: ...

    def bind_disconnect(self, handler: Callable[[str], Awaitable[None]]) -> None: ...

    async def send_audio(self, audio: bytes) -> None: ...

    async def clear_audio(self) -> None: ...

    async def send_event(self, event: Mapping[str, Any]) -> None: ...

    async def close(self) -> None: ...
