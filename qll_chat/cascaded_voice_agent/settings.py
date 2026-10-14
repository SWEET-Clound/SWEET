"""第一版自研级联语音服务配置。"""

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class CascadedVoiceSettings:
    """只保存算法服务需要的配置，敏感值由环境变量注入。"""

    aliyun_api_key: str
    aliyun_stt_model: str
    cosyvoice_model: str
    cosyvoice_voice: str
    agent_chat_url: str
    internal_service_token: str
    host: str = "0.0.0.0"
    port: int = 8010
    agent_timeout_seconds: float = 120.0
    session_idle_seconds: float = 300.0
    session_token_ttl_seconds: float = 120.0
    tts_chunk_max_chars: int = 80
    vad_energy_threshold: int = 450
    vad_start_ms: int = 120
    vad_stop_ms: int = 600
    audio_in_sample_rate: int = 16000
    audio_out_sample_rate: int = 24000
    webrtc_ice_urls: tuple[str, ...] = ()
    webrtc_ice_username: str = ""
    webrtc_ice_credential: str = ""

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "CascadedVoiceSettings":
        """从仓库现有 HOCON 配置结果构造设置。"""
        return cls(
            aliyun_api_key=str(config.get("aliyun_api_key", "")),
            aliyun_stt_model=str(config.get("aliyun_stt_model", "")),
            cosyvoice_model=str(config.get("cosyvoice_model", "")),
            cosyvoice_voice=str(config.get("cosyvoice_voice", "longxiaochun_v2")),
            agent_chat_url=str(config.get("agent_chat_url", "")),
            internal_service_token=str(config.get("internal_service_token", "")),
            host=str(config.get("host", "0.0.0.0")),
            port=int(config.get("port", 8010)),
            agent_timeout_seconds=float(config.get("agent_timeout_seconds", 120.0)),
            session_idle_seconds=float(config.get("session_idle_seconds", 300.0)),
            session_token_ttl_seconds=float(config.get("session_token_ttl_seconds", 120.0)),
            tts_chunk_max_chars=int(config.get("tts_chunk_max_chars", 80)),
            vad_energy_threshold=int(config.get("vad_energy_threshold", 450)),
            vad_start_ms=int(config.get("vad_start_ms", 120)),
            vad_stop_ms=int(config.get("vad_stop_ms", 600)),
            audio_in_sample_rate=int(config.get("audio_in_sample_rate", 16000)),
            audio_out_sample_rate=int(config.get("audio_out_sample_rate", 24000)),
            webrtc_ice_urls=cls._parse_ice_urls(config.get("webrtc_ice_urls", "")),
            webrtc_ice_username=str(config.get("webrtc_ice_username", "")),
            webrtc_ice_credential=str(config.get("webrtc_ice_credential", "")),
        )

    def validate(self) -> None:
        missing = [
            name
            for name in (
                "aliyun_api_key",
                "aliyun_stt_model",
                "cosyvoice_model",
                "cosyvoice_voice",
                "agent_chat_url",
                "internal_service_token",
            )
            if not str(getattr(self, name) or "").strip()
        ]
        if missing:
            raise ValueError(f"级联语音配置缺少字段：{', '.join(missing)}")
        if not 1 <= self.port <= 65535:
            raise ValueError("语音服务端口必须在 1～65535 之间")
        agent_url = urlsplit(self.agent_chat_url)
        if agent_url.scheme not in {"http", "https"} or not agent_url.netloc:
            raise ValueError("agent_chat_url 必须是有效的 HTTP(S) 地址")
        if self.agent_timeout_seconds <= 0 or self.session_idle_seconds <= 0:
            raise ValueError("超时时间必须大于 0")
        if self.session_token_ttl_seconds <= 0:
            raise ValueError("会话令牌有效期必须大于 0")
        if self.tts_chunk_max_chars <= 0:
            raise ValueError("tts_chunk_max_chars 必须大于 0")
        if (
            self.vad_energy_threshold <= 0
            or self.vad_start_ms <= 0
            or self.vad_stop_ms <= 0
        ):
            raise ValueError("VAD 阈值和起止时长必须大于 0")
        if self.audio_in_sample_rate != 16000:
            raise ValueError("第一版 ASR 输入固定为 16 kHz 单声道 PCM")
        if self.audio_out_sample_rate != 24000:
            raise ValueError("第一版 TTS 输出固定为 24 kHz 单声道 PCM")
        if bool(self.webrtc_ice_username.strip()) != bool(
            self.webrtc_ice_credential.strip()
        ):
            raise ValueError("WebRTC ICE 用户名和凭证必须同时配置")
        allowed_schemes = ("stun:", "stuns:", "turn:", "turns:")
        if any(
            not url.lower().startswith(allowed_schemes)
            for url in self.webrtc_ice_urls
        ):
            raise ValueError("WebRTC ICE 地址只允许 STUN/TURN 协议")

    def ice_servers(self) -> tuple[Mapping[str, Any], ...]:
        """构造只来自服务端配置的 aiortc ICE Server 参数。"""
        if not self.webrtc_ice_urls:
            return ()
        server: dict[str, Any] = {"urls": list(self.webrtc_ice_urls)}
        if self.webrtc_ice_username:
            server["username"] = self.webrtc_ice_username
            server["credential"] = self.webrtc_ice_credential
        return (server,)

    @staticmethod
    def _parse_ice_urls(value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            values = value.split(",")
        elif isinstance(value, (list, tuple)):
            values = value
        else:
            raise ValueError("webrtc_ice_urls 必须是逗号分隔字符串或数组")
        return tuple(str(item).strip() for item in values if str(item).strip())
