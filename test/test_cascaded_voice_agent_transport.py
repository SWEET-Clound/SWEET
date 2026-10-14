"""WebRTC 媒体会话的本机双 PeerConnection 集成测试。"""

import asyncio
import fractions
import importlib.util
import unittest


_AIORTC_AVAILABLE = (
    importlib.util.find_spec("aiortc") is not None
    and importlib.util.find_spec("av") is not None
)

if _AIORTC_AVAILABLE:
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import AudioStreamTrack
    from av import AudioFrame

    from qll_chat.cascaded_voice_agent.transport import WebRTCMediaSession


    class _ClientAudioTrack(AudioStreamTrack):
        def __init__(self) -> None:
            super().__init__()
            self._timestamp = 0

        async def recv(self) -> AudioFrame:
            await asyncio.sleep(0.02)
            frame = AudioFrame(format="s16", layout="mono", samples=320)
            for plane in frame.planes:
                plane.update(bytes(640))
            frame.sample_rate = 16000
            frame.pts = self._timestamp
            frame.time_base = fractions.Fraction(1, 16000)
            self._timestamp += 320
            return frame


async def _wait(event: asyncio.Event, label: str) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=8)
    except TimeoutError as exc:
        raise AssertionError(f"等待 {label} 超时") from exc


@unittest.skipUnless(_AIORTC_AVAILABLE, "需要安装 aiortc 和 av")
class WebRTCMediaSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_transfers_audio_and_events_then_releases_tasks(self) -> None:
        client = RTCPeerConnection()
        server = WebRTCMediaSession("webrtc-integration-test")
        upstream = asyncio.Event()
        downstream = asyncio.Event()
        control = asyncio.Event()
        messages: list[str] = []
        decoded_rates: list[int] = []

        async def on_audio(audio: bytes) -> None:
            self.assertTrue(audio)
            self.assertEqual(len(audio) % 2, 0)
            upstream.set()

        server.bind_audio_input(on_audio)
        client.addTrack(_ClientAudioTrack())
        channel = client.createDataChannel("voice-events")

        @channel.on("message")
        def on_message(message) -> None:
            messages.append(str(message))
            control.set()

        @client.on("track")
        def on_track(track) -> None:
            async def read_once() -> None:
                frame = await track.recv()
                self.assertGreater(frame.samples, 0)
                decoded_rates.append(frame.sample_rate)
                downstream.set()

            asyncio.create_task(read_once())

        try:
            await server.send_event({"type": "session.ready"})
            offer = await client.createOffer()
            await client.setLocalDescription(offer)
            assert client.localDescription is not None
            answer = await server.accept_offer(
                client.localDescription.sdp,
                client.localDescription.type,
            )
            await client.setRemoteDescription(
                RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
            )
            await server.send_audio(bytes(4800))

            await _wait(upstream, "上行音频")
            await _wait(downstream, "下行音频")
            await _wait(control, "DataChannel 事件")
            self.assertTrue(any("session.ready" in message for message in messages))
            self.assertGreater(decoded_rates[0], 0)

            with self.assertRaisesRegex(ValueError, "只能提交一次"):
                await server.accept_offer(
                    client.localDescription.sdp,
                    client.localDescription.type,
                )
        finally:
            await client.close()
            await server.close()
            await asyncio.sleep(0)

        leaked = [
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and not task.done()
            and task.get_name().startswith("voice:")
        ]
        self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
