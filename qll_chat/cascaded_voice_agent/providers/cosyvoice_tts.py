"""CosyVoice 流式 TTS SDK 的异步封装。"""

import asyncio
import re
import threading
from concurrent.futures import Future
from importlib import metadata
from typing import Any, Awaitable, Callable


_MINIMUM_CANCEL_VERSION = (1, 26, 4)


class _TTSCallback:
    """把 SDK 工作线程里的音频和错误切回 asyncio 事件循环。"""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_audio: Callable[[bytes], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
    ) -> None:
        self._loop = loop
        self._on_audio = on_audio
        self._on_error = on_error
        self._is_active: Callable[[], bool] = lambda: True
        self._pending_audio = bytearray()
        self._pending_callbacks: set[Future[Any]] = set()
        self._lock = threading.Lock()

    def bind_activity_check(self, is_active: Callable[[], bool]) -> None:
        """绑定当前任务检查，丢弃取消后迟到的 SDK 回调。"""
        self._is_active = is_active

    def on_open(self) -> None:
        """连接成功后等待 SDK 接收文本。"""
        return None

    def on_complete(self) -> None:
        """PCM16 不应遗留半个采样；发现异常时交给上层定位。"""
        with self._lock:
            has_incomplete_sample = bool(self._pending_audio)
            self._pending_audio.clear()
        if has_incomplete_sample and self._is_active():
            self._submit(self._on_error("CosyVoice 返回了不完整的 PCM16 采样"))

    def on_close(self) -> None:
        """连接关闭不需要额外事件。"""
        return None

    def on_event(self, message: str) -> None:
        """首版不向前端暴露供应商内部事件。"""
        return None

    def on_data(self, data: bytes) -> None:
        if not self._is_active() or not data:
            return
        with self._lock:
            self._pending_audio.extend(data)
            aligned_size = len(self._pending_audio) // 2 * 2
            if not aligned_size:
                return
            audio = bytes(self._pending_audio[:aligned_size])
            del self._pending_audio[:aligned_size]
        self._submit(self._on_audio(audio))

    def on_error(self, message) -> None:
        if self._is_active():
            self._submit(self._on_error(str(message)))

    def _submit(self, coroutine) -> None:
        """事件循环关闭后安全丢弃回调，避免产生未等待协程。"""
        if self._loop.is_closed():
            coroutine.close()
            return
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError:
            coroutine.close()
            return
        with self._lock:
            self._pending_callbacks.add(future)
        future.add_done_callback(self._discard_callback)

    def _discard_callback(self, future: Future[Any]) -> None:
        with self._lock:
            self._pending_callbacks.discard(future)

    async def drain(self) -> None:
        """等待 streaming_complete 之前已提交的音频回调真正下传。"""
        while True:
            with self._lock:
                pending = tuple(self._pending_callbacks)
            if not pending:
                return
            await asyncio.gather(
                *(asyncio.wrap_future(item) for item in pending),
                return_exceptions=True,
            )

    def cancel_pending(self) -> None:
        """取消已经排入事件循环、但尚未完成的旧话轮回调。"""
        with self._lock:
            pending = tuple(self._pending_callbacks)
        for future in pending:
            future.cancel()


class CosyVoiceTTSService:
    """每个播报话轮创建一个 SDK 任务，支持完成和立即取消。"""

    def __init__(self, api_key: str, model: str, voice: str) -> None:
        if not str(api_key or "").strip():
            raise ValueError("CosyVoice 缺少 API Key")
        if not str(model or "").strip():
            raise ValueError("CosyVoice 缺少模型名称")
        if not str(voice or "").strip():
            raise ValueError("CosyVoice 缺少音色名称")
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._synthesizer: Any = None
        self._callback: _TTSCallback | None = None
        self._generation = 0
        self._state_lock = asyncio.Lock()

    async def start(
        self,
        on_audio: Callable[[bytes], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
    ) -> None:
        import dashscope
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

        self._validate_sdk_version()
        await self.cancel()
        dashscope.api_key = self._api_key
        self._generation += 1
        generation = self._generation
        callback = _TTSCallback(asyncio.get_running_loop(), on_audio, on_error)
        callback.bind_activity_check(
            lambda: generation == self._generation and self._synthesizer is not None
        )
        synthesizer = SpeechSynthesizer(
            model=self._model,
            voice=self._voice,
            format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            callback=callback,
        )
        async with self._state_lock:
            self._synthesizer = synthesizer
            self._callback = callback

    async def send_text(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("CosyVoice 文本必须是字符串")
        if not text.strip():
            return
        if self._synthesizer is None:
            raise RuntimeError("CosyVoice 任务尚未启动")
        await asyncio.to_thread(self._synthesizer.streaming_call, text)

    async def finish(self, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("CosyVoice 完成超时必须大于 0")
        async with self._state_lock:
            synthesizer = self._synthesizer
            callback = self._callback
        if synthesizer is None:
            return
        try:
            # 官方 streaming_complete 不接收超时参数，客户端超时由 asyncio 控制。
            await asyncio.wait_for(
                asyncio.to_thread(synthesizer.streaming_complete),
                timeout=timeout_seconds,
            )
            if callback is not None:
                await callback.drain()
        except TimeoutError as exc:
            await self._cancel_synthesizer(synthesizer)
            raise RuntimeError("CosyVoice 流式合成完成超时") from exc
        finally:
            async with self._state_lock:
                if self._synthesizer is synthesizer:
                    self._synthesizer = None
                    self._callback = None
                    self._generation += 1

    async def cancel(self) -> None:
        async with self._state_lock:
            synthesizer, self._synthesizer = self._synthesizer, None
            callback, self._callback = self._callback, None
            self._generation += 1
        if callback is not None:
            callback.cancel_pending()
        if synthesizer is not None:
            await self._cancel_synthesizer(synthesizer)

    async def close(self) -> None:
        await self.cancel()

    async def _cancel_synthesizer(self, synthesizer: Any) -> None:
        """等待服务端确认取消，确保旧话轮不会继续占用连接。"""
        await asyncio.to_thread(synthesizer.streaming_cancel, 10_000)

    @staticmethod
    def _validate_sdk_version() -> None:
        """取消能力要求 DashScope 1.26.4+，旧版本不能满足插话语义。"""
        try:
            raw_version = metadata.version("dashscope")
        except metadata.PackageNotFoundError:
            # 测试替身可能只注入模块；真实运行时的 import 会先暴露缺包。
            return
        numbers = tuple(int(part) for part in re.findall(r"\d+", raw_version)[:3])
        normalized = numbers + (0,) * (3 - len(numbers))
        if normalized < _MINIMUM_CANCEL_VERSION:
            required = ".".join(str(part) for part in _MINIMUM_CANCEL_VERSION)
            raise RuntimeError(
                f"CosyVoice 可中断合成要求 dashscope>={required}，"
                f"当前版本为 {raw_version}"
            )
