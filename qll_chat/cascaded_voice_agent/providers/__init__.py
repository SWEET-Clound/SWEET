"""级联语音流水线使用的云语音供应商适配器。"""

from .aliyun_stt import AliyunStreamingSTTProcessor
from .cosyvoice_tts import CosyVoiceTTSService

__all__ = ["AliyunStreamingSTTProcessor", "CosyVoiceTTSService"]
