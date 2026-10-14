"""把现有 qll_chat `/chat` SSE 链路接入语音流水线。"""

import asyncio
import copy
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import datetime
from typing import Any, Mapping

import aiohttp

from .contracts import TrustedUserContext
from .frames import (
    AgentEndFrame,
    AgentTextFrame,
    ErrorFrame,
    InterruptFrame,
    StateFrame,
    TranscriptFrame,
)
from .processor import FrameProcessor


class ExistingAgentSSEClient:
    """固定路由到 VoiceAgent，只替换本轮问题、标识和最小历史。"""

    def __init__(
        self,
        url: str,
        trusted_context: TrustedUserContext,
        *,
        timeout_seconds: float,
        max_history_turns: int = 20,
    ) -> None:
        trusted_context.validate()
        self._url = url
        self._trusted_context = trusted_context
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_history_turns = max_history_turns
        self._history = list(trusted_context.agent_request_template.get("history", []))
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def stream(self, question: str) -> AsyncIterator[Mapping[str, Any]]:
        await self.open()
        assert self._session is not None
        request_data = self._build_request(question)
        async with self._session.post(self._url, json=request_data) as response:
            if response.status != 200:
                raise RuntimeError(f"现有 Agent 服务返回 HTTP {response.status}")
            async for payload in self._iter_sse(response):
                yield payload

    def append_history(self, question: str, answer: str, function_name: str) -> None:
        self._history.append(
            {
                "id": uuid.uuid4().hex,
                "question": question,
                "answer": answer,
                "functionName": function_name,
                "askTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        self._history = self._history[-self._max_history_turns :]

    def _build_request(self, question: str) -> dict[str, Any]:
        request_data = copy.deepcopy(dict(self._trusted_context.agent_request_template))
        request_data.update(
            {
                "tenantId": self._trusted_context.tenant_id,
                "userId": self._trusted_context.user_id,
                # 路由属于算法服务安全策略，不能由旧模板或手机输入降级为其他 Agent。
                "functionName": "voiceAgent",
                "question": question,
                "questionId": str(time.time_ns()),
                "answerId": str(time.time_ns() + 1),
                "history": copy.deepcopy(self._history),
            }
        )
        return request_data

    @staticmethod
    async def _iter_sse(response: aiohttp.ClientResponse) -> AsyncIterator[Mapping[str, Any]]:
        data_lines: list[str] = []
        while not response.content.at_eof():
            raw = await response.content.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                if data_lines:
                    data = "\n".join(data_lines)
                    data_lines.clear()
                    payload = ExistingAgentSSEClient._decode_sse_data(data)
                    if payload is not None:
                        yield payload
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            payload = ExistingAgentSSEClient._decode_sse_data("\n".join(data_lines))
            if payload is not None:
                yield payload

    @staticmethod
    def _decode_sse_data(data: str) -> Mapping[str, Any] | None:
        if data.strip() == "[DONE]":
            return None
        payload = json.loads(data)
        return payload if isinstance(payload, dict) else None


class ExistingAgentProcessor(FrameProcessor):
    """把 ASR final 交给现有 Agent，并将最终可播报文本流向 TTS。"""

    def __init__(self, client: ExistingAgentSSEClient) -> None:
        super().__init__("existing-agent")
        self._client = client
        self._turn_task: asyncio.Task[None] | None = None

    async def process_frame(self, frame) -> None:
        if isinstance(frame, InterruptFrame):
            # 先清空下游播放缓冲，再等待 HTTP 流完成取消。
            await self.push_frame(frame)
            await self._cancel_turn()
            return
        if isinstance(frame, TranscriptFrame) and frame.is_final and frame.text.strip():
            await self._cancel_turn()
            self._turn_task = asyncio.create_task(
                self._run_turn(frame.turn_id, frame.text.strip()),
                name=f"voice:agent:{frame.turn_id}",
            )
            return
        await self.push_frame(frame)

    async def on_stop(self) -> None:
        await self._cancel_turn()
        await self._client.close()

    async def _run_turn(self, turn_id: int, question: str) -> None:
        streamed_text: list[str] = []
        final_answer = ""
        function_name = "generalChat"
        references: tuple[Mapping[str, Any], ...] = ()
        await self.push_frame(StateFrame(event="assistant.thinking", data={"turn_id": turn_id}))
        try:
            stream = self._client.stream(question)
            async with aclosing(stream):
                async for payload in stream:
                    try:
                        status = int(payload.get("status", -1))
                    except (TypeError, ValueError):
                        continue
                    if status in (3, 4):
                        await self.push_frame(
                            StateFrame(
                                event="assistant.tool",
                                data={"turn_id": turn_id, "status": status},
                            )
                        )
                    elif status == 0:
                        token = str(payload.get("token", ""))
                        if token:
                            streamed_text.append(token)
                            await self.push_frame(
                                AgentTextFrame(turn_id=turn_id, text=token)
                            )
                    elif status == 1:
                        token = str(payload.get("token", ""))
                        if token:
                            streamed_text.append(token)
                            await self.push_frame(
                                AgentTextFrame(turn_id=turn_id, text=token)
                            )
                        final_answer = str(payload.get("answer", "")).strip()
                        function_name = str(
                            (payload.get("extraData") or {}).get(
                                "functionName", "generalChat"
                            )
                        )
                        references = tuple(payload.get("reference_data") or ())
                        break
                    elif status == 11:
                        raise RuntimeError(
                            "第一版语音链路暂不支持需要客户端续跑的工具"
                        )

            if not final_answer:
                final_answer = "".join(streamed_text).strip()
            if not final_answer:
                raise RuntimeError("现有 Agent 流结束但没有最终答案")
            if not streamed_text:
                await self.push_frame(AgentTextFrame(turn_id=turn_id, text=final_answer))
            self._client.append_history(question, final_answer, function_name)
            await self.push_frame(
                AgentEndFrame(turn_id=turn_id, answer=final_answer, references=references)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.push_frame(
                ErrorFrame(
                    component="existing-agent",
                    message=str(exc),
                    fatal=False,
                    turn_id=turn_id,
                )
            )
            fallback = "抱歉，这次处理没有成功，请稍后再试。"
            await self.push_frame(AgentTextFrame(turn_id=turn_id, text=fallback))
            await self.push_frame(AgentEndFrame(turn_id=turn_id, answer=fallback))

    async def _cancel_turn(self) -> None:
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            try:
                await self._turn_task
            except asyncio.CancelledError:
                pass
        self._turn_task = None
