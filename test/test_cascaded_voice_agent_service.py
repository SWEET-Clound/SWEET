"""级联语音服务 HTTP 信任边界测试。"""

import asyncio
import unittest

from aiohttp.test_utils import TestClient, TestServer

from qll_chat.cascaded_voice_agent.settings import CascadedVoiceSettings
from services.cascaded_voice_agent_service import create_app


def _settings() -> CascadedVoiceSettings:
    return CascadedVoiceSettings(
        aliyun_api_key="test-key",
        aliyun_stt_model="paraformer-realtime-v2",
        cosyvoice_model="cosyvoice-v2",
        cosyvoice_voice="longxiaochun_v2",
        agent_chat_url="http://127.0.0.1:1801/chat",
        internal_service_token="internal-test-token",
        session_token_ttl_seconds=60,
    )


def _create_body(session_id: str = "service-test") -> dict:
    return {
        "session_id": session_id,
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "agent_request": {
            "tenantId": "tenant-1",
            "userId": "user-1",
            "channel": "writer",
            "functionName": "voiceAgent",
            "algExtra": {},
        },
    }


class _MemoryMedia:
    def __init__(self, session_id: str, **kwargs) -> None:
        self.session_id = session_id
        self.options = kwargs
        self.close_count = 0
        self.disconnect_handler = None

    async def accept_offer(self, sdp: str, description_type: str) -> dict[str, str]:
        return {"sdp": "test-answer", "type": "answer"}

    def bind_disconnect(self, handler) -> None:
        self.disconnect_handler = handler

    async def close(self) -> None:
        self.close_count += 1


class _MemoryVoiceSession:
    def __init__(self, settings: CascadedVoiceSettings) -> None:
        self.settings = settings
        self.run_count = 0
        self.close_reasons: list[str] = []
        self._closed = asyncio.Event()

    async def run(self, media, context) -> None:
        self.run_count += 1

    async def close(self, reason: str) -> None:
        self.close_reasons.append(reason)
        self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()


class CascadedVoiceAgentServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.media: list[_MemoryMedia] = []
        self.sessions: list[_MemoryVoiceSession] = []

        def media_factory(session_id: str, **kwargs) -> _MemoryMedia:
            media = _MemoryMedia(session_id, **kwargs)
            self.media.append(media)
            return media

        def session_factory(settings: CascadedVoiceSettings) -> _MemoryVoiceSession:
            session = _MemoryVoiceSession(settings)
            self.sessions.append(session)
            return session

        app = create_app(
            _settings(),
            media_factory=media_factory,
            session_factory=session_factory,
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_session_token_is_single_use_and_client_cannot_set_ice(self) -> None:
        unauthorized = await self.client.post("/voice/sessions", json=_create_body())
        self.assertEqual(unauthorized.status, 401)

        mismatched = _create_body("mismatched-identity")
        mismatched["agent_request"]["tenantId"] = "another-tenant"
        rejected_identity = await self.client.post(
            "/voice/sessions",
            json=mismatched,
            headers={"X-Internal-Service-Token": "internal-test-token"},
        )
        self.assertEqual(rejected_identity.status, 400)

        created = await self.client.post(
            "/voice/sessions",
            json=_create_body(),
            headers={"X-Internal-Service-Token": "internal-test-token"},
        )
        self.assertEqual(created.status, 201)
        registration = await created.json()
        token = registration["voice_token"]
        headers = {"Authorization": f"Bearer {token}"}
        offer_url = registration["signaling_url"]

        wrong_token = await self.client.post(
            offer_url,
            json={"sdp": "test-offer", "type": "offer"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(wrong_token.status, 401)

        client_ice = await self.client.post(
            offer_url,
            json={
                "sdp": "test-offer",
                "type": "offer",
                "ice_servers": [{"urls": "turn:untrusted.example"}],
            },
            headers=headers,
        )
        self.assertEqual(client_ice.status, 400)

        accepted = await self.client.post(
            offer_url,
            json={"sdp": "test-offer", "type": "offer"},
            headers=headers,
        )
        self.assertEqual(accepted.status, 200)
        self.assertEqual(
            await accepted.json(),
            {"sdp": "test-answer", "type": "answer"},
        )
        self.assertEqual(len(self.media), 1)
        self.assertEqual(len(self.sessions), 1)
        self.assertEqual(self.sessions[0].run_count, 1)

        repeated = await self.client.post(
            offer_url,
            json={"sdp": "test-offer", "type": "offer"},
            headers=headers,
        )
        self.assertEqual(repeated.status, 400)

        closed = await self.client.delete(
            f"/voice/sessions/{registration['session_id']}",
            headers=headers,
        )
        self.assertEqual(closed.status, 200)
        self.assertEqual(self.sessions[0].close_reasons, ["client_requested"])

        reused = await self.client.post(
            offer_url,
            json={"sdp": "test-offer", "type": "offer"},
            headers=headers,
        )
        self.assertEqual(reused.status, 401)


if __name__ == "__main__":
    unittest.main()
