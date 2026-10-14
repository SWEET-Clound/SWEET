"""第一版自研级联语音服务：可信建会话、WebRTC 信令和资源回收。"""

import asyncio
import os
import re
import secrets
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from aiohttp import web

from project_utils.generic_utils.config_utils import load_config_tree
from qll_chat.cascaded_voice_agent import CascadedVoiceSession, TrustedUserContext
from qll_chat.cascaded_voice_agent.session_registry import VoiceSessionRegistry
from qll_chat.cascaded_voice_agent.settings import CascadedVoiceSettings


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _load_settings() -> CascadedVoiceSettings:
    env = os.getenv("CHAT_ENV", "dev")
    config_file = f"resources/application/cascaded_voice_agent/configs/{env}.conf"
    config = load_config_tree([config_file]).as_plain_ordered_dict()
    settings = CascadedVoiceSettings.from_mapping(config["cascaded_voice_agent"])
    settings.validate()
    return settings


def _bearer_token(request: web.Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise web.HTTPUnauthorized(text="缺少语音会话 Bearer Token")
    return token


def _required_text(body: Mapping[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def create_app(
    settings: CascadedVoiceSettings | None = None,
    *,
    media_factory: Callable[..., Any] | None = None,
    session_factory: Callable[[CascadedVoiceSettings], CascadedVoiceSession] = (
        CascadedVoiceSession
    ),
) -> web.Application:
    """创建 aiohttp 应用；WebRTC 长连接不放入 Flask/Gunicorn 同步 worker。"""
    settings = settings or _load_settings()
    settings.validate()
    if media_factory is None:
        from qll_chat.cascaded_voice_agent.transport import WebRTCMediaSession

        media_factory = WebRTCMediaSession
    registry = VoiceSessionRegistry(settings.session_token_ttl_seconds)
    sessions: dict[str, CascadedVoiceSession] = {}
    cleanup_tasks: set[asyncio.Task[None]] = set()

    @web.middleware
    async def error_middleware(request: web.Request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except PermissionError as exc:
            raise web.HTTPUnauthorized(text=str(exc)) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc

    app = web.Application(middlewares=[error_middleware], client_max_size=512 * 1024)

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "service": "cascaded_voice_agent"})

    async def create_voice_session(request: web.Request) -> web.Response:
        supplied_token = request.headers.get("X-Internal-Service-Token", "")
        if not secrets.compare_digest(supplied_token, settings.internal_service_token):
            raise web.HTTPUnauthorized(text="内部服务凭证无效")
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("创建语音会话的请求体必须是对象")
        session_id = body.get("session_id") or uuid.uuid4().hex
        if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("session_id 只允许 1～64 位字母、数字、下划线或连字符")
        agent_request = body.get("agent_request")
        if not isinstance(agent_request, Mapping):
            raise ValueError("agent_request 必须是可信业务后端生成的对象")
        context = TrustedUserContext(
            tenant_id=_required_text(body, "tenant_id"),
            user_id=_required_text(body, "user_id"),
            voice_session_id=session_id,
            agent_request_template=agent_request,
        )
        registration = await registry.create(context)
        return web.json_response(
            {
                "session_id": session_id,
                "voice_token": registration.token,
                "expires_at": registration.expires_at,
                "signaling_url": f"/voice/sessions/{session_id}/offer",
            },
            status=201,
        )

    async def exchange_offer(request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        token = _bearer_token(request)
        await registry.authenticate(session_id, token)
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("WebRTC offer 请求体必须是对象")
        if "ice_servers" in body:
            raise ValueError("ICE/TURN 只能由语音服务端配置")
        sdp = _required_text(body, "sdp")
        description_type = str(body.get("type", "offer"))
        registration = await registry.authenticate(
            session_id, token, mark_connected=True
        )
        media: Any = None
        voice_session: CascadedVoiceSession | None = None

        async def close_session(reason: str) -> None:
            if voice_session is not None:
                await voice_session.close(reason)
                if sessions.get(session_id) is voice_session:
                    sessions.pop(session_id, None)
            elif media is not None:
                await media.close()
            await registry.remove(session_id)

        try:
            media = media_factory(
                session_id,
                input_sample_rate=settings.audio_in_sample_rate,
                output_sample_rate=settings.audio_out_sample_rate,
                ice_servers=settings.ice_servers(),
            )
            voice_session = session_factory(settings)
            answer = await media.accept_offer(sdp, description_type)
            sessions[session_id] = voice_session
            await voice_session.run(media, registration.context)
            media.bind_disconnect(close_session)
            cleanup_task = asyncio.create_task(
                _cleanup_when_closed(session_id, voice_session),
                name=f"voice:registry-cleanup:{session_id}",
            )
            cleanup_tasks.add(cleanup_task)
            cleanup_task.add_done_callback(cleanup_tasks.discard)
            return web.json_response(answer)
        except BaseException:
            await close_session("offer_failed")
            raise

    async def _cleanup_when_closed(
        session_id: str,
        voice_session: CascadedVoiceSession,
    ) -> None:
        await voice_session.wait_closed()
        if sessions.get(session_id) is voice_session:
            sessions.pop(session_id, None)
        await registry.remove(session_id)

    async def close_voice_session(request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        await registry.authenticate(session_id, _bearer_token(request))
        voice_session = sessions.get(session_id)
        if voice_session is not None:
            await voice_session.close("client_requested")
        sessions.pop(session_id, None)
        await registry.remove(session_id)
        return web.json_response({"session_id": session_id, "closed": True})

    async def shutdown(_: web.Application) -> None:
        await asyncio.gather(
            *(session.close("server_shutdown") for session in tuple(sessions.values())),
            return_exceptions=True,
        )
        sessions.clear()
        if cleanup_tasks:
            await asyncio.gather(*tuple(cleanup_tasks), return_exceptions=True)

    app.router.add_get("/health", health)
    app.router.add_post("/voice/sessions", create_voice_session)
    app.router.add_post("/voice/sessions/{session_id}/offer", exchange_offer)
    app.router.add_delete("/voice/sessions/{session_id}", close_voice_session)
    app.on_shutdown.append(shutdown)
    return app


if __name__ == "__main__":
    _settings = _load_settings()
    app = create_app(_settings)
    web.run_app(app, host=_settings.host, port=_settings.port)
