"""基于 aiortc 的手机端一对一 WebRTC 媒体会话。"""

import asyncio
import fractions
import json
import logging
import time
from collections import deque
from contextlib import suppress
from typing import Any, Awaitable, Callable, Mapping

import numpy as np
from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError
from av import AudioFrame, AudioResampler


logger = logging.getLogger(__name__)


class RawPCMOutputTrack(AudioStreamTrack):
    """按 10 ms 节奏把 24 kHz 单声道 PCM 放入 WebRTC 音轨。"""

    def __init__(self, sample_rate: int = 24000) -> None:
        if sample_rate <= 0 or sample_rate % 100:
            raise ValueError("WebRTC 输出采样率必须是 100 的正整数倍")
        super().__init__()
        self._sample_rate = sample_rate
        self._samples_per_frame = sample_rate // 100
        self._bytes_per_frame = self._samples_per_frame * 2
        self._timestamp = 0
        self._started_at: float | None = None
        self._audio = bytearray()
        self._max_buffer_bytes = sample_rate * 2 * 5

    async def add_audio(self, audio: bytes) -> None:
        if not isinstance(audio, bytes) or len(audio) % 2:
            raise ValueError("WebRTC 输出必须是完整的 PCM16 bytes")
        self._audio.extend(audio)
        if len(self._audio) > self._max_buffer_bytes:
            # shortcut: 首版保留最近 5 秒，P9 根据真实播放积压增加指标告警。
            overflow = len(self._audio) - self._max_buffer_bytes
            del self._audio[: overflow + overflow % 2]

    async def clear(self) -> None:
        self._audio.clear()

    async def recv(self) -> AudioFrame:
        if self._started_at is None:
            self._started_at = time.monotonic()
        if self._timestamp:
            wait = self._started_at + self._timestamp / self._sample_rate - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)

        if len(self._audio) >= self._bytes_per_frame:
            chunk = bytes(self._audio[: self._bytes_per_frame])
            del self._audio[: self._bytes_per_frame]
        elif self._audio:
            chunk = bytes(self._audio).ljust(self._bytes_per_frame, b"\x00")
            self._audio.clear()
        else:
            chunk = bytes(self._bytes_per_frame)

        samples = np.frombuffer(chunk, dtype=np.int16)
        frame = AudioFrame.from_ndarray(samples[None, :], layout="mono")
        frame.sample_rate = self._sample_rate
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, self._sample_rate)
        self._timestamp += self._samples_per_frame
        return frame


class WebRTCMediaSession:
    """负责 SDP、输入重采样、输出音轨和状态 DataChannel。"""

    def __init__(
        self,
        session_id: str,
        *,
        input_sample_rate: int = 16000,
        output_sample_rate: int = 24000,
        ice_servers: tuple[Mapping[str, Any], ...] = (),
    ) -> None:
        if not session_id.strip():
            raise ValueError("WebRTC session_id 不能为空")
        if input_sample_rate != 16000 or output_sample_rate != 24000:
            raise ValueError("第一版 WebRTC 固定使用 16 kHz 输入和 24 kHz 输出")
        self.session_id = session_id
        rtc_ice_servers = [
            RTCIceServer(
                urls=item.get("urls"),
                username=item.get("username"),
                credential=item.get("credential"),
            )
            for item in ice_servers
        ]
        self._pc = RTCPeerConnection(RTCConfiguration(iceServers=rtc_ice_servers))
        self._input_sample_rate = input_sample_rate
        self._output_track = RawPCMOutputTrack(output_sample_rate)
        self._pc.addTrack(self._output_track)
        self._audio_handler: Callable[[bytes], Awaitable[None]] | None = None
        self._disconnect_handler: Callable[[str], Awaitable[None]] | None = None
        self._data_channel = None
        self._pending_events: deque[str] = deque(maxlen=100)
        self._reader_tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._offer_accepted = False
        self._disconnect_notified = False
        self._install_handlers()

    def bind_audio_input(self, handler: Callable[[bytes], Awaitable[None]]) -> None:
        self._audio_handler = handler

    def bind_disconnect(self, handler: Callable[[str], Awaitable[None]]) -> None:
        self._disconnect_handler = handler

    async def accept_offer(self, sdp: str, description_type: str) -> dict[str, str]:
        if description_type != "offer":
            raise ValueError("WebRTC description type 必须是 offer")
        if self._offer_accepted:
            raise ValueError("WebRTC offer 只能提交一次")
        if not isinstance(sdp, str) or not sdp.strip():
            raise ValueError("WebRTC SDP offer 不能为空")
        if len(sdp.encode("utf-8")) > 256 * 1024:
            raise ValueError("WebRTC SDP offer 超过 256 KiB")
        self._offer_accepted = True
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp, type=description_type)
        )
        answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(answer)
        local = self._pc.localDescription
        if local is None:
            raise RuntimeError("WebRTC 未生成本地 SDP answer")
        return {"sdp": local.sdp, "type": local.type, "session_id": self.session_id}

    async def send_audio(self, audio: bytes) -> None:
        if not self._closed:
            await self._output_track.add_audio(audio)

    async def clear_audio(self) -> None:
        await self._output_track.clear()

    async def send_event(self, event: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(event), ensure_ascii=False)
        channel = self._data_channel
        if channel is not None and channel.readyState == "open":
            channel.send(payload)
        elif not self._closed:
            self._pending_events.append(payload)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        current_task = asyncio.current_task()
        tasks = tuple(task for task in self._reader_tasks if task is not current_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._reader_tasks.clear()
        self._pending_events.clear()
        self._output_track.stop()
        await self._pc.close()

    def _install_handlers(self) -> None:
        @self._pc.on("track")
        def on_track(track) -> None:
            if track.kind != "audio":
                return
            task = asyncio.create_task(
                self._read_audio(track),
                name=f"voice:webrtc-input:{self.session_id}",
            )
            self._reader_tasks.add(task)
            task.add_done_callback(self._reader_tasks.discard)

        @self._pc.on("datachannel")
        def on_datachannel(channel) -> None:
            self._data_channel = channel

            @channel.on("open")
            def on_open() -> None:
                self._flush_pending_events(channel)

            self._flush_pending_events(channel)

        @self._pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            state = self._pc.connectionState
            if (
                not self._closed
                and state in {"failed", "closed", "disconnected"}
            ):
                await self._notify_disconnect(state)

    async def _read_audio(self, track) -> None:
        resampler = AudioResampler(format="s16", layout="mono", rate=self._input_sample_rate)
        try:
            while not self._closed:
                frame = await track.recv()
                for converted in resampler.resample(frame):
                    pcm = converted.to_ndarray().astype(np.int16).tobytes()
                    if self._audio_handler is not None:
                        await self._audio_handler(pcm)
        except MediaStreamError:
            await self._notify_disconnect("audio_ended")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WebRTC 输入音轨读取失败 | session_id=%s", self.session_id)
            await self._notify_disconnect("audio_failed")

    async def _notify_disconnect(self, reason: str) -> None:
        if self._disconnect_notified or self._closed:
            return
        self._disconnect_notified = True
        if self._disconnect_handler is not None:
            await self._disconnect_handler(reason)

    def _flush_pending_events(self, channel) -> None:
        while self._pending_events and channel.readyState == "open":
            channel.send(self._pending_events.popleft())
