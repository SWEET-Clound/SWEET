"""P4 真实 `/chat` 与微搜联调；默认跳过，避免误用真实账号和额度。"""

import copy
import asyncio
import json
import os
import time
import unittest
from pathlib import Path
from typing import Any, AsyncIterator, Mapping


RUN_LIVE = os.getenv("RUN_CASCADED_VOICE_LIVE") == "1"
RUN_CLOUD_LIVE = os.getenv("RUN_CASCADED_VOICE_CLOUD_LIVE") == "1"
RUN_PIPELINE_LIVE = os.getenv("RUN_CASCADED_VOICE_PIPELINE_LIVE") == "1"


@unittest.skipUnless(RUN_LIVE, "设置 RUN_CASCADED_VOICE_LIVE=1 后才执行真实联调")
class CascadedVoiceAgentLiveTest(unittest.IsolatedAsyncioTestCase):
    """每次完整运行会产生两次普通问答和一次微搜调用。"""

    @classmethod
    def setUpClass(cls) -> None:
        required = (
            "VOICE_AGENT_CHAT_URL",
            "CASCADED_VOICE_AGENT_REQUEST_FILE",
            "CASCADED_VOICE_PLAIN_QUESTION",
            "CASCADED_VOICE_WEISOU_QUESTION",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise RuntimeError(
                "已开启真实联调，但缺少环境变量：" + ", ".join(missing)
            )

        cls.chat_url = os.environ["VOICE_AGENT_CHAT_URL"]
        request_file = Path(os.environ["CASCADED_VOICE_AGENT_REQUEST_FILE"])
        cls.template = json.loads(request_file.read_text(encoding="utf-8"))
        if not isinstance(cls.template, dict):
            raise ValueError("可信 agent_request 文件顶层必须是 JSON 对象")
        cls.plain_question = os.environ["CASCADED_VOICE_PLAIN_QUESTION"].strip()
        cls.weisou_question = os.environ["CASCADED_VOICE_WEISOU_QUESTION"].strip()
        if not cls.plain_question or not cls.weisou_question:
            raise ValueError("真实联调问题不能为空")

    async def asyncSetUp(self) -> None:
        from qll_chat.cascaded_voice_agent.contracts import TrustedUserContext
        from qll_chat.cascaded_voice_agent.existing_agent import ExistingAgentSSEClient

        tenant_id = str(self.template.get("tenantId", ""))
        user_id = str(self.template.get("userId", ""))
        context = TrustedUserContext(
            tenant_id=tenant_id,
            user_id=user_id,
            voice_session_id=f"live-p4-{time.time_ns()}",
            agent_request_template=self.template,
        )
        self.client = ExistingAgentSSEClient(
            self.chat_url,
            context,
            timeout_seconds=120,
        )

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_direct_and_adapter_paths_both_return_answers(self) -> None:
        direct_payloads = await self._direct_chat(self.plain_question)
        adapted_payloads = await self._collect(self.client.stream(self.plain_question))

        self.assertTrue(self._final_answer(direct_payloads))
        self.assertTrue(self._final_answer(adapted_payloads))
        self.assertEqual(self._statuses(direct_payloads)[-1], 1)
        self.assertEqual(self._statuses(adapted_payloads)[-1], 1)

    async def test_weisou_has_tool_lifecycle_and_only_allowed_tools(self) -> None:
        payloads = await self._collect(self.client.stream(self.weisou_question))
        statuses = self._statuses(payloads)

        start = statuses.index(3)
        end = statuses.index(4, start + 1)
        self.assertTrue(any(status in (0, 1) for status in statuses[end + 1 :]))
        self.assertEqual(statuses[-1], 1)
        self.assertTrue(self._final_answer(payloads))

        tool_names = {
            str(call.get("safe_name") or (call.get("function") or {}).get("name") or "")
            for payload in payloads
            if self._status(payload) == 3
            for call in payload.get("tool_calls", [])
            if isinstance(call, Mapping)
        }
        self.assertTrue(tool_names, "status=3 未返回可核验的工具名")
        self.assertLessEqual(
            tool_names,
            {"search_weisou", "read_weisou_document"},
        )

    async def _direct_chat(self, question: str) -> list[Mapping[str, Any]]:
        import aiohttp

        from qll_chat.cascaded_voice_agent.existing_agent import ExistingAgentSSEClient

        request_data = copy.deepcopy(self.template)
        request_data.update(
            {
                "functionName": "voiceAgent",
                "question": question,
                "questionId": str(time.time_ns()),
                "answerId": str(time.time_ns() + 1),
            }
        )
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.chat_url, json=request_data) as response:
                self.assertEqual(response.status, 200, f"/chat 返回 HTTP {response.status}")
                return [
                    payload
                    async for payload in ExistingAgentSSEClient._iter_sse(response)
                ]

    @staticmethod
    async def _collect(
        stream: AsyncIterator[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        return [payload async for payload in stream]

    @staticmethod
    def _status(payload: Mapping[str, Any]) -> int:
        try:
            return int(payload.get("status", -1))
        except (TypeError, ValueError):
            return -1

    @classmethod
    def _statuses(cls, payloads: list[Mapping[str, Any]]) -> list[int]:
        return [status for payload in payloads if (status := cls._status(payload)) >= 0]

    @classmethod
    def _final_answer(cls, payloads: list[Mapping[str, Any]]) -> str:
        tokens: list[str] = []
        for payload in payloads:
            status = cls._status(payload)
            if status in (0, 1):
                token = str(payload.get("token", ""))
                if token:
                    tokens.append(token)
            if status == 1:
                return str(payload.get("answer", "")).strip() or "".join(tokens).strip()
        return ""


@unittest.skipUnless(
    RUN_CLOUD_LIVE,
    "设置 RUN_CASCADED_VOICE_CLOUD_LIVE=1 后才执行真实语音云服务联调",
)
class CascadedVoiceCloudLiveTest(unittest.IsolatedAsyncioTestCase):
    """通过公开接口验证 P5 ASR 和 P6 TTS，不保存用户音频或合成结果。"""

    @classmethod
    def setUpClass(cls) -> None:
        required = ("DASHSCOPE_API_KEY", "CASCADED_VOICE_ASR_PCM_FILE")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise RuntimeError(
                "已开启真实语音云服务联调，但缺少环境变量：" + ", ".join(missing)
            )
        cls.api_key = os.environ["DASHSCOPE_API_KEY"]
        cls.asr_model = os.getenv(
            "CASCADED_VOICE_ASR_MODEL", "paraformer-realtime-v2"
        )
        cls.tts_model = os.getenv("CASCADED_VOICE_TTS_MODEL", "cosyvoice-v2")
        cls.tts_voice = os.getenv(
            "CASCADED_VOICE_TTS_VOICE", "longxiaochun_v2"
        )
        cls.tts_text = os.getenv(
            "CASCADED_VOICE_TTS_TEXT", "你好，这是一段级联语音测试。"
        ).strip()
        cls.pcm = Path(os.environ["CASCADED_VOICE_ASR_PCM_FILE"]).read_bytes()
        if not cls.pcm or len(cls.pcm) % 2:
            raise ValueError("ASR 测试文件必须是非空的 16 kHz 单声道 PCM16")
        if len(cls.tts_text) < 2:
            raise ValueError("TTS 测试文本至少需要两个字符，以验证增量输入")

    async def test_asr_emits_one_final_for_each_of_two_turns(self) -> None:
        from qll_chat.cascaded_voice_agent.frames import (
            ErrorFrame,
            InputAudioFrame,
            TranscriptFrame,
            UserSpeechStartedFrame,
            UserSpeechStoppedFrame,
        )
        from qll_chat.cascaded_voice_agent.pipeline import VoicePipeline
        from qll_chat.cascaded_voice_agent.processor import CallbackProcessor
        from qll_chat.cascaded_voice_agent.providers.aliyun_stt import (
            AliyunStreamingSTTProcessor,
        )

        frames = []
        final_received = asyncio.Event()

        async def collect(frame) -> None:
            frames.append(frame)
            if isinstance(frame, TranscriptFrame) and frame.is_final:
                final_received.set()

        pipeline = VoicePipeline(
            [
                AliyunStreamingSTTProcessor(self.api_key, self.asr_model),
                CallbackProcessor(collect),
            ],
            session_id="live-p5-asr",
        )
        await pipeline.start()
        try:
            for turn_id in (1, 2):
                final_received.clear()
                await pipeline.queue_frame(UserSpeechStartedFrame(turn_id=turn_id))
                for offset in range(0, len(self.pcm), 3200):
                    chunk = self.pcm[offset : offset + 3200]
                    await pipeline.queue_frame(InputAudioFrame(audio=chunk))
                    await asyncio.sleep(len(chunk) / 32000)
                await pipeline.queue_frame(UserSpeechStoppedFrame(turn_id=turn_id))
                await asyncio.wait_for(final_received.wait(), timeout=60)
        finally:
            await pipeline.stop("live-test-complete")

        errors = [frame for frame in frames if isinstance(frame, ErrorFrame)]
        finals = [
            frame
            for frame in frames
            if isinstance(frame, TranscriptFrame) and frame.is_final
        ]
        self.assertEqual(errors, [])
        self.assertEqual([frame.turn_id for frame in finals], [1, 2])
        self.assertTrue(all(frame.text.strip() for frame in finals))

    async def test_tts_streams_aligned_audio_before_completion(self) -> None:
        from qll_chat.cascaded_voice_agent.providers.cosyvoice_tts import (
            CosyVoiceTTSService,
        )

        chunks: list[bytes] = []
        errors: list[str] = []
        first_audio = asyncio.Event()

        async def on_audio(chunk: bytes) -> None:
            chunks.append(chunk)
            first_audio.set()

        async def on_error(message: str) -> None:
            errors.append(message)

        service = CosyVoiceTTSService(
            self.api_key,
            self.tts_model,
            self.tts_voice,
        )
        await service.start(on_audio, on_error)
        try:
            split_at = len(self.tts_text) // 2
            await service.send_text(self.tts_text[:split_at])
            await asyncio.wait_for(first_audio.wait(), timeout=30)
            await service.send_text(self.tts_text[split_at:])
            await service.finish(timeout_seconds=30)
        finally:
            await service.close()

        self.assertEqual(errors, [])
        self.assertTrue(chunks)
        self.assertTrue(all(chunk and len(chunk) % 2 == 0 for chunk in chunks))


class _LiveMemoryMedia:
    """P7 只替换传输层，算法处理器全部使用真实实现。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.events: list[dict[str, Any]] = []
        self.audio_bytes = 0
        self._audio_handler = None
        self._disconnect_handler = None

    def bind_audio_input(self, handler) -> None:
        self._audio_handler = handler

    def bind_disconnect(self, handler) -> None:
        self._disconnect_handler = handler

    async def receive_audio(self, audio: bytes) -> None:
        if self._audio_handler is None:
            raise RuntimeError("媒体会话尚未绑定音频入口")
        await self._audio_handler(audio)

    async def send_audio(self, audio: bytes) -> None:
        self.audio_bytes += len(audio)

    async def clear_audio(self) -> None:
        return None

    async def send_event(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))

    async def close(self) -> None:
        return None


@unittest.skipUnless(
    RUN_PIPELINE_LIVE,
    "设置 RUN_CASCADED_VOICE_PIPELINE_LIVE=1 后才执行 P7 真实级联联调",
)
class CascadedVoicePipelineLiveTest(unittest.IsolatedAsyncioTestCase):
    """用内存传输验证真实 VAD→ASR→Agent→TTS，隔离 WebRTC 变量。"""

    @classmethod
    def setUpClass(cls) -> None:
        required = (
            "DASHSCOPE_API_KEY",
            "VOICE_AGENT_CHAT_URL",
            "CASCADED_VOICE_AGENT_REQUEST_FILE",
            "CASCADED_VOICE_ASR_PCM_FILE",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise RuntimeError(
                "已开启 P7 真实级联联调，但缺少环境变量：" + ", ".join(missing)
            )
        cls.api_key = os.environ["DASHSCOPE_API_KEY"]
        cls.chat_url = os.environ["VOICE_AGENT_CHAT_URL"]
        cls.template = json.loads(
            Path(os.environ["CASCADED_VOICE_AGENT_REQUEST_FILE"]).read_text(
                encoding="utf-8"
            )
        )
        cls.pcm = Path(os.environ["CASCADED_VOICE_ASR_PCM_FILE"]).read_bytes()
        cls.vad_threshold = int(os.getenv("CASCADED_VOICE_VAD_THRESHOLD", "450"))
        if not isinstance(cls.template, dict):
            raise ValueError("可信 agent_request 文件顶层必须是 JSON 对象")
        if not cls.pcm or len(cls.pcm) % 2:
            raise ValueError("P7 输入必须是非空的 16 kHz 单声道 PCM16")

    async def test_real_cascade_returns_audio_and_completion(self) -> None:
        import audioop

        from qll_chat.cascaded_voice_agent.contracts import TrustedUserContext
        from qll_chat.cascaded_voice_agent.session import CascadedVoiceSession
        from qll_chat.cascaded_voice_agent.settings import CascadedVoiceSettings

        chunks = [
            self.pcm[offset : offset + 640]
            for offset in range(0, len(self.pcm), 640)
        ]
        self.assertGreaterEqual(
            max(audioop.rms(chunk, 2) for chunk in chunks if len(chunk) % 2 == 0),
            self.vad_threshold,
            "PCM 响度未达到 VAD 阈值",
        )

        session_id = f"live-p7-{time.time_ns()}"
        media = _LiveMemoryMedia(session_id)
        completed = asyncio.Event()
        playback_finished = asyncio.Event()
        original_send_event = media.send_event

        async def send_event(event: Mapping[str, Any]) -> None:
            await original_send_event(event)
            if event.get("type") == "assistant.answer.completed":
                completed.set()
            if (
                event.get("type") == "assistant.listening"
                and event.get("turn_id") == 1
            ):
                playback_finished.set()

        media.send_event = send_event
        settings = CascadedVoiceSettings(
            aliyun_api_key=self.api_key,
            aliyun_stt_model=os.getenv(
                "CASCADED_VOICE_ASR_MODEL", "paraformer-realtime-v2"
            ),
            cosyvoice_model=os.getenv(
                "CASCADED_VOICE_TTS_MODEL", "cosyvoice-v2"
            ),
            cosyvoice_voice=os.getenv(
                "CASCADED_VOICE_TTS_VOICE", "longxiaochun_v2"
            ),
            agent_chat_url=self.chat_url,
            internal_service_token="live-test-only",
            session_idle_seconds=180,
            vad_energy_threshold=self.vad_threshold,
        )
        user = TrustedUserContext(
            tenant_id=str(self.template.get("tenantId", "")),
            user_id=str(self.template.get("userId", "")),
            voice_session_id=session_id,
            agent_request_template=self.template,
        )
        session = CascadedVoiceSession(settings)
        await session.run(media, user)
        try:
            for chunk in chunks:
                await media.receive_audio(chunk)
                await asyncio.sleep(len(chunk) / 32000)
            for _ in range(30):
                await media.receive_audio(b"\x00\x00" * 320)
                await asyncio.sleep(0.02)
            await asyncio.wait_for(completed.wait(), timeout=180)
            await asyncio.wait_for(playback_finished.wait(), timeout=60)
        finally:
            await session.close("live-test-complete")

        errors = [
            event for event in media.events if event.get("type") == "session.error"
        ]
        self.assertEqual(errors, [])
        self.assertGreater(media.audio_bytes, 0)
        self.assertFalse(
            [
                task.get_name()
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
                and not task.done()
                and task.get_name().startswith("voice:")
            ]
        )


if __name__ == "__main__":
    unittest.main()
