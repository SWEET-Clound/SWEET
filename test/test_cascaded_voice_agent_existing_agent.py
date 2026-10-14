"""现有 `/chat` SSE 与语音 Agent 适配器的行为测试。"""

import asyncio
import json
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

from qll_chat.cascaded_voice_agent.contracts import TrustedUserContext
from qll_chat.cascaded_voice_agent.existing_agent import (
    ExistingAgentProcessor,
    ExistingAgentSSEClient,
)
from qll_chat.cascaded_voice_agent.frames import (
    AgentEndFrame,
    AgentTextFrame,
    ErrorFrame,
    InterruptFrame,
    StateFrame,
    TranscriptFrame,
)
from qll_chat.cascaded_voice_agent.pipeline import VoicePipeline
from qll_chat.cascaded_voice_agent.processor import CallbackProcessor


def _trusted_context() -> TrustedUserContext:
    return TrustedUserContext(
        tenant_id="tenant-1",
        user_id="user-1",
        voice_session_id="voice-1",
        agent_request_template={
            "tenantId": "tenant-1",
            "userId": "user-1",
            "channel": "writer",
            "functionName": "agentChat",
            "algExtra": {"sceneMcp": {"enable": 1}},
            "history": [],
        },
    )


class ExistingAgentSSEClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_untrusted_identity_before_opening_http_session(self) -> None:
        context = TrustedUserContext(
            tenant_id="tenant-1",
            user_id="user-1",
            voice_session_id="voice-1",
            agent_request_template={
                "tenantId": "another-tenant",
                "userId": "user-1",
                "channel": "writer",
                "functionName": "agentChat",
                "algExtra": {},
            },
        )

        with self.assertRaisesRegex(ValueError, "身份与可信上下文不一致"):
            ExistingAgentSSEClient(
                "http://127.0.0.1/chat",
                context,
                timeout_seconds=1,
            )

    async def test_parses_chunked_sse_and_ignores_terminal_done(self) -> None:
        async def chat(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await response.prepare(request)
            await response.write(b": keepalive\n\n")
            await response.write(b'data: {"status":"0",')
            await response.write('"token":"你好"}\n\n'.encode())
            await response.write(b"data: [DONE]")
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post("/chat", chat)
        server = TestServer(app)
        await server.start_server()
        client = ExistingAgentSSEClient(
            str(server.make_url("/chat")),
            _trusted_context(),
            timeout_seconds=1,
        )
        try:
            payloads = [payload async for payload in client.stream("你好")]
        finally:
            await client.close()
            await server.close()

        self.assertEqual(payloads, [{"status": "0", "token": "你好"}])


class ExistingAgentProcessorTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_failure_becomes_one_turn_scoped_fallback(self) -> None:
        async def chat(request: web.Request) -> web.Response:
            return web.Response(status=503, text="internal-secret-marker")

        app = web.Application()
        app.router.add_post("/chat", chat)
        server = TestServer(app)
        await server.start_server()
        frames = []
        completed = asyncio.Event()

        async def collect(frame) -> None:
            frames.append(frame)
            if isinstance(frame, AgentEndFrame):
                completed.set()

        client = ExistingAgentSSEClient(
            str(server.make_url("/chat")),
            _trusted_context(),
            timeout_seconds=1,
        )
        pipeline = VoicePipeline(
            [ExistingAgentProcessor(client), CallbackProcessor(collect)],
            "agent-http-error-test",
        )
        await pipeline.start()
        try:
            await pipeline.queue_frame(
                TranscriptFrame(turn_id=6, text="查询失败场景", is_final=True)
            )
            await asyncio.wait_for(completed.wait(), timeout=1)
        finally:
            await pipeline.stop("test-complete")
            await server.close()

        errors = [frame for frame in frames if isinstance(frame, ErrorFrame)]
        fallbacks = [frame for frame in frames if isinstance(frame, AgentTextFrame)]
        endings = [frame for frame in frames if isinstance(frame, AgentEndFrame)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].turn_id, 6)
        self.assertIn("503", errors[0].message)
        self.assertNotIn("internal-secret-marker", errors[0].message)
        self.assertEqual(len(fallbacks), 1)
        self.assertEqual(len(endings), 1)
        self.assertEqual(fallbacks[0].text, endings[0].answer)

    async def test_interrupt_cancels_inflight_turn_without_fallback_output(self) -> None:
        request_started = asyncio.Event()
        release_server = asyncio.Event()

        async def chat(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await response.prepare(request)
            request_started.set()
            await release_server.wait()
            try:
                await response.write(
                    'data: {"status":"1","answer":"旧答案"}\n\n'.encode()
                )
                await response.write_eof()
            except ConnectionResetError:
                pass
            return response

        app = web.Application()
        app.router.add_post("/chat", chat)
        server = TestServer(app)
        await server.start_server()
        frames = []
        interrupted = asyncio.Event()

        async def collect(frame) -> None:
            frames.append(frame)
            if isinstance(frame, InterruptFrame):
                interrupted.set()

        client = ExistingAgentSSEClient(
            str(server.make_url("/chat")),
            _trusted_context(),
            timeout_seconds=1,
        )
        pipeline = VoicePipeline(
            [ExistingAgentProcessor(client), CallbackProcessor(collect)],
            "agent-interrupt-test",
        )
        await pipeline.start()
        try:
            await pipeline.queue_frame(
                TranscriptFrame(turn_id=1, text="旧问题", is_final=True)
            )
            await asyncio.wait_for(request_started.wait(), timeout=1)
            await pipeline.queue_frame(InterruptFrame(turn_id=2))
            await asyncio.wait_for(interrupted.wait(), timeout=1)
            release_server.set()
            await asyncio.sleep(0.05)
        finally:
            release_server.set()
            await pipeline.stop("test-complete")
            await server.close()

        stale_outputs = [
            frame
            for frame in frames
            if isinstance(frame, (AgentTextFrame, AgentEndFrame, ErrorFrame))
        ]
        self.assertEqual(stale_outputs, [])

    async def test_completed_turn_is_added_to_the_next_trusted_request(self) -> None:
        requests = []

        async def chat(request: web.Request) -> web.StreamResponse:
            body = await request.json()
            requests.append(body)
            answer = f"回答{len(requests)}"
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await response.prepare(request)
            await response.write(
                (
                    "data: "
                    + json.dumps(
                        {
                            "status": "1",
                            "answer": answer,
                            "extraData": {"functionName": "agentChat"},
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                ).encode()
            )
            await response.write_eof()
            return response

        template = dict(_trusted_context().agent_request_template)
        context = TrustedUserContext(
            tenant_id="tenant-1",
            user_id="user-1",
            voice_session_id="voice-1",
            agent_request_template=template,
        )
        app = web.Application()
        app.router.add_post("/chat", chat)
        server = TestServer(app)
        await server.start_server()
        completed = asyncio.Event()

        async def collect(frame) -> None:
            if isinstance(frame, AgentEndFrame):
                completed.set()

        client = ExistingAgentSSEClient(
            str(server.make_url("/chat")),
            context,
            timeout_seconds=1,
        )
        pipeline = VoicePipeline(
            [ExistingAgentProcessor(client), CallbackProcessor(collect)],
            "agent-history-test",
        )
        await pipeline.start()
        try:
            for turn_id, question in ((1, "问题一"), (2, "问题二")):
                completed.clear()
                await pipeline.queue_frame(
                    TranscriptFrame(turn_id=turn_id, text=question, is_final=True)
                )
                await asyncio.wait_for(completed.wait(), timeout=1)
        finally:
            await pipeline.stop("test-complete")
            await server.close()

        self.assertEqual(requests[0]["question"], "问题一")
        self.assertEqual(requests[1]["question"], "问题二")
        self.assertEqual(requests[0]["tenantId"], "tenant-1")
        self.assertEqual(requests[0]["userId"], "user-1")
        self.assertEqual(requests[0]["functionName"], "voiceAgent")
        self.assertEqual(len(requests[1]["history"]), 1)
        self.assertEqual(requests[1]["history"][0]["question"], "问题一")
        self.assertEqual(requests[1]["history"][0]["answer"], "回答1")
        self.assertEqual(requests[1]["history"][0]["functionName"], "agentChat")
        self.assertTrue(requests[1]["history"][0]["id"])
        self.assertTrue(requests[1]["history"][0]["askTime"])
        self.assertEqual(template["history"], [])

    async def test_only_tool_statuses_are_reported_as_tool_activity(self) -> None:
        async def chat(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await response.prepare(request)
            for payload in (
                '{"status":"100","keepalive":{"phase":"agent"}}',
                '{"status":"2","reasoning_token":"分析中"}',
                '{"status":"3","tool_calls":[]}',
                '{"status":"4","tool_response":{}}',
                '{"status":"1","answer":"完成"}',
            ):
                await response.write(f"data: {payload}\n\n".encode())
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post("/chat", chat)
        server = TestServer(app)
        await server.start_server()
        frames = []
        completed = asyncio.Event()

        async def collect(frame) -> None:
            frames.append(frame)
            if isinstance(frame, AgentEndFrame):
                completed.set()

        client = ExistingAgentSSEClient(
            str(server.make_url("/chat")),
            _trusted_context(),
            timeout_seconds=1,
        )
        pipeline = VoicePipeline(
            [ExistingAgentProcessor(client), CallbackProcessor(collect)],
            "agent-status-test",
        )
        await pipeline.start()
        try:
            await pipeline.queue_frame(
                TranscriptFrame(turn_id=5, text="查询资料", is_final=True)
            )
            await asyncio.wait_for(completed.wait(), timeout=1)
        finally:
            await pipeline.stop("test-complete")
            await server.close()

        tool_statuses = [
            frame.data["status"]
            for frame in frames
            if isinstance(frame, StateFrame) and frame.event == "assistant.tool"
        ]
        self.assertEqual(tool_statuses, [3, 4])

    async def test_status_eleven_returns_turn_scoped_unsupported_error(self) -> None:
        async def chat(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await response.prepare(request)
            await response.write(
                'data: {"status":"11","resumeType":"clientTool"}\n\n'.encode()
            )
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post("/chat", chat)
        server = TestServer(app)
        await server.start_server()
        frames = []
        completed = asyncio.Event()

        async def collect(frame) -> None:
            frames.append(frame)
            if isinstance(frame, AgentEndFrame):
                completed.set()

        client = ExistingAgentSSEClient(
            str(server.make_url("/chat")),
            _trusted_context(),
            timeout_seconds=1,
        )
        pipeline = VoicePipeline(
            [ExistingAgentProcessor(client), CallbackProcessor(collect)],
            "agent-client-tool-test",
        )
        await pipeline.start()
        try:
            await pipeline.queue_frame(
                TranscriptFrame(turn_id=9, text="需要客户端操作", is_final=True)
            )
            await asyncio.wait_for(completed.wait(), timeout=1)
        finally:
            await pipeline.stop("test-complete")
            await server.close()

        errors = [frame for frame in frames if isinstance(frame, ErrorFrame)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].component, "existing-agent")
        self.assertEqual(errors[0].turn_id, 9)
        self.assertIn("不支持", errors[0].message)

    async def test_status_one_completes_without_waiting_for_socket_close(self) -> None:
        release_server = asyncio.Event()

        async def chat(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await response.prepare(request)
            await response.write(
                'data: {"status":"1","token":"完成","answer":"完成"}\n\n'.encode()
            )
            await release_server.wait()
            try:
                await response.write_eof()
            except ConnectionResetError:
                pass
            return response

        app = web.Application()
        app.router.add_post("/chat", chat)
        server = TestServer(app)
        await server.start_server()
        completed = asyncio.Event()

        async def collect(frame) -> None:
            if isinstance(frame, AgentEndFrame):
                completed.set()

        client = ExistingAgentSSEClient(
            str(server.make_url("/chat")),
            _trusted_context(),
            timeout_seconds=1,
        )
        pipeline = VoicePipeline(
            [ExistingAgentProcessor(client), CallbackProcessor(collect)],
            "agent-terminal-test",
        )
        await pipeline.start()
        try:
            await pipeline.queue_frame(
                TranscriptFrame(turn_id=3, text="完成任务", is_final=True)
            )
            await asyncio.wait_for(completed.wait(), timeout=0.2)
        finally:
            release_server.set()
            await pipeline.stop("test-complete")
            await server.close()

    async def test_status_one_tail_is_included_in_spoken_text(self) -> None:
        async def chat(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await response.prepare(request)
            await response.write(
                'data: {"status":"0","token":"前半"}\n\n'.encode()
            )
            await response.write(
                (
                    'data: {"status":"1","token":"后半","answer":"前半后半",'
                    '"extraData":{"functionName":"agentChat"}}\n\n'
                ).encode()
            )
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post("/chat", chat)
        server = TestServer(app)
        await server.start_server()

        frames = []
        completed = asyncio.Event()

        async def collect(frame) -> None:
            frames.append(frame)
            if isinstance(frame, AgentEndFrame):
                completed.set()

        client = ExistingAgentSSEClient(
            str(server.make_url("/chat")),
            _trusted_context(),
            timeout_seconds=1,
        )
        pipeline = VoicePipeline(
            [ExistingAgentProcessor(client), CallbackProcessor(collect)],
            "agent-tail-test",
        )
        await pipeline.start()
        try:
            await pipeline.queue_frame(
                TranscriptFrame(turn_id=7, text="问题", is_final=True)
            )
            await asyncio.wait_for(completed.wait(), timeout=1)
        finally:
            await pipeline.stop("test-complete")
            await server.close()

        self.assertEqual(
            [frame.text for frame in frames if isinstance(frame, AgentTextFrame)],
            ["前半", "后半"],
        )


if __name__ == "__main__":
    unittest.main()
