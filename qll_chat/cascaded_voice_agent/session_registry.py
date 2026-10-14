"""短期语音令牌和进程内会话注册表。"""

import asyncio
import secrets
import time
from dataclasses import dataclass, field

from .contracts import TrustedUserContext


@dataclass(slots=True)
class SessionRegistration:
    context: TrustedUserContext
    token: str = field(repr=False)
    expires_at: float
    connected: bool = False


class VoiceSessionRegistry:
    """第一版单进程注册表；服务重启后客户端必须重新创建会话。"""

    def __init__(self, token_ttl_seconds: float) -> None:
        if token_ttl_seconds <= 0:
            raise ValueError("语音会话令牌有效期必须大于 0")
        self._ttl = token_ttl_seconds
        self._items: dict[str, SessionRegistration] = {}
        self._lock = asyncio.Lock()

    async def create(self, context: TrustedUserContext) -> SessionRegistration:
        context.validate()
        async with self._lock:
            self._purge_expired()
            if context.voice_session_id in self._items:
                raise ValueError("voice_session_id 已存在")
            registration = SessionRegistration(
                context=context,
                token=secrets.token_urlsafe(32),
                expires_at=time.time() + self._ttl,
            )
            self._items[context.voice_session_id] = registration
            return registration

    async def authenticate(
        self,
        session_id: str,
        token: str,
        *,
        mark_connected: bool = False,
    ) -> SessionRegistration:
        if not session_id or not token:
            raise PermissionError("语音会话令牌无效或已过期")
        async with self._lock:
            self._purge_expired()
            registration = self._items.get(session_id)
            if registration is None or not secrets.compare_digest(registration.token, token):
                raise PermissionError("语音会话令牌无效或已过期")
            if mark_connected and registration.connected:
                raise ValueError("语音会话已经连接")
            if mark_connected:
                registration.connected = True
            return registration

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            self._items.pop(session_id, None)

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [
            key
            for key, value in self._items.items()
            if not value.connected and value.expires_at <= now
        ]
        for key in expired:
            self._items.pop(key, None)
