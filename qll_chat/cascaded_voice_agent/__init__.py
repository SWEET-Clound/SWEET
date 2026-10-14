"""自研编排的第一版级联语音 Agent。"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .contracts import MediaSession, TrustedUserContext, VoiceSessionState
    from .session import CascadedVoiceSession
    from .settings import CascadedVoiceSettings

__all__ = [
    "CascadedVoiceSession",
    "CascadedVoiceSettings",
    "MediaSession",
    "TrustedUserContext",
    "VoiceSessionState",
]


def __getattr__(name: str) -> Any:
    """延迟加载高层模块，避免核心编排被云 SDK 依赖污染。"""
    if name == "CascadedVoiceSession":
        from .session import CascadedVoiceSession

        return CascadedVoiceSession
    if name == "CascadedVoiceSettings":
        from .settings import CascadedVoiceSettings

        return CascadedVoiceSettings
    if name in {"MediaSession", "TrustedUserContext", "VoiceSessionState"}:
        from .contracts import MediaSession, TrustedUserContext, VoiceSessionState

        return {
            "MediaSession": MediaSession,
            "TrustedUserContext": TrustedUserContext,
            "VoiceSessionState": VoiceSessionState,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
