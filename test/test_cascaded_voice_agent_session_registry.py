"""级联语音短期会话令牌测试。"""

import unittest
from unittest.mock import patch

from qll_chat.cascaded_voice_agent.contracts import TrustedUserContext
from qll_chat.cascaded_voice_agent.session_registry import VoiceSessionRegistry


def _context(session_id: str = "voice-registry-test") -> TrustedUserContext:
    return TrustedUserContext(
        tenant_id="tenant-1",
        user_id="user-1",
        voice_session_id=session_id,
        agent_request_template={
            "tenantId": "tenant-1",
            "userId": "user-1",
            "channel": "writer",
            "functionName": "voiceAgent",
            "algExtra": {},
        },
    )


class VoiceSessionRegistryTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_wrong_token_and_second_connection(self) -> None:
        registry = VoiceSessionRegistry(token_ttl_seconds=60)
        registration = await registry.create(_context())

        with self.assertRaises(PermissionError):
            await registry.authenticate("voice-registry-test", "wrong-token")

        authenticated = await registry.authenticate(
            "voice-registry-test",
            registration.token,
            mark_connected=True,
        )
        self.assertIs(authenticated, registration)

        with self.assertRaisesRegex(ValueError, "已经连接"):
            await registry.authenticate(
                "voice-registry-test",
                registration.token,
                mark_connected=True,
            )

    async def test_removed_token_cannot_be_reused(self) -> None:
        registry = VoiceSessionRegistry(token_ttl_seconds=60)
        registration = await registry.create(_context())
        await registry.remove("voice-registry-test")

        with self.assertRaises(PermissionError):
            await registry.authenticate(
                "voice-registry-test",
                registration.token,
            )

    async def test_unconnected_token_expires_after_ttl(self) -> None:
        registry = VoiceSessionRegistry(token_ttl_seconds=60)
        with patch(
            "qll_chat.cascaded_voice_agent.session_registry.time.time",
            return_value=1000,
        ):
            registration = await registry.create(_context())

        with patch(
            "qll_chat.cascaded_voice_agent.session_registry.time.time",
            return_value=1061,
        ):
            with self.assertRaises(PermissionError):
                await registry.authenticate(
                    "voice-registry-test",
                    registration.token,
                )


if __name__ == "__main__":
    unittest.main()
