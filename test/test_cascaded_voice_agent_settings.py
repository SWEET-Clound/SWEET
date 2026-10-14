"""级联语音服务启动配置的边界测试。"""

import unittest
from dataclasses import replace

from qll_chat.cascaded_voice_agent.settings import CascadedVoiceSettings


def _valid_settings() -> CascadedVoiceSettings:
    return CascadedVoiceSettings(
        aliyun_api_key="test-key",
        aliyun_stt_model="paraformer-realtime-v2",
        cosyvoice_model="cosyvoice-v2",
        cosyvoice_voice="longxiaochun_v2",
        agent_chat_url="http://127.0.0.1:1801/chat",
        internal_service_token="internal-token",
    )


class CascadedVoiceSettingsTest(unittest.TestCase):
    """非法配置必须在创建会话前失败。"""

    def test_accepts_minimum_valid_settings(self) -> None:
        _valid_settings().validate()

    def test_rejects_invalid_startup_values(self) -> None:
        invalid_cases = (
            ({"port": 0}, "端口"),
            ({"port": 65536}, "端口"),
            ({"agent_chat_url": "127.0.0.1/chat"}, "HTTP"),
            ({"agent_chat_url": "ftp://127.0.0.1/chat"}, "HTTP"),
            ({"vad_energy_threshold": 0}, "VAD"),
            ({"vad_start_ms": 0}, "VAD"),
            ({"vad_stop_ms": 0}, "VAD"),
            ({"aliyun_api_key": "   "}, "缺少字段"),
        )

        for changes, message in invalid_cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(
                ValueError, message
            ):
                replace(_valid_settings(), **changes).validate()

    def test_ice_credentials_must_be_configured_as_a_pair(self) -> None:
        with self.assertRaisesRegex(ValueError, "同时配置"):
            replace(
                _valid_settings(),
                webrtc_ice_urls=("turn:turn.example.com:3478",),
                webrtc_ice_username="voice-user",
            ).validate()

    def test_accepts_case_insensitive_stun_and_turn_schemes(self) -> None:
        replace(
            _valid_settings(),
            webrtc_ice_urls=("STUN:stun.example.com:3478",),
        ).validate()


if __name__ == "__main__":
    unittest.main()
