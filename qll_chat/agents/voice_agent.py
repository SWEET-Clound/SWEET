#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音交互主智能体：复用 BaseToolAgent 循环，首版只开放微搜配套工具。"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List

from project_utils.text_process.file_operation import load_markdown
from qll_chat.agents.base_tool_agent import AgentRunState, BaseToolAgent, PromptSpec, ToolMeta
from qll_chat.cascaded_voice_agent.tool_policy import (
    ALLOWED_VOICE_TOOL_NAMES,
    filter_voice_tools,
)
from qll_chat.core.context_builder import ContextBuilder
from qll_chat.mcp.mcp_search import MCPSearch
from qll_chat.mcp.mcp_tool_client import MCPToolClient
from qll_chat.prompts.xiaoe_chat import XiaoeChat
from qll_chat.retrieval.weisou_context import prepare_weisou_context
from qll_chat.retrieval.weisou_system_type_client import WeisouSystemTypeClient
from qll_chat.utils.weisou_modules import (
    build_weisou_e9_module_registry,
    build_weisou_module_registry,
)


logger = logging.getLogger(__name__)


class VoiceAgent(BaseToolAgent):
    """面向语音播报的 LLM Agent，首版只允许调用微搜查询和正文读取。"""

    _ALLOWED_TOOL_NAMES = ALLOWED_VOICE_TOOL_NAMES
    _ENABLED_LOCAL_TOOLS: List[str] = []

    def __init__(self, config: Dict[str, Any], xiaoe_chat_config: Dict[str, Any]) -> None:
        super().__init__(
            mcp_tool_client=MCPToolClient(config),
            xiaoe_chat=XiaoeChat(xiaoe_chat_config),
            context_builder=ContextBuilder(),
            config=config,
        )
        self.mcp_search = MCPSearch(config)
        self.system_prompt = load_markdown(
            "resources/common_data/prompt_template/voice_agent.md"
        )
        self.weisou_module_registry = build_weisou_module_registry(
            config.get("weisou_modules")
        )
        self.weisou_e9_module_registry = build_weisou_e9_module_registry(
            config.get("weisou_e9_modules")
        )
        self.weisou_system_type_client = WeisouSystemTypeClient(
            url=str(config.get("weisou_system_type_url") or ""),
            timeout=float(config.get("weisou_system_type_timeout", 3.0)),
        )
        self.weaver_tenant_ids = {
            str(item) for item in config.get("weaver_tenantId", [])
        }
        self.deploy_env = str(config.get("deploy_env") or "").strip().lower()
        # 首版不接入动态 provider，避免它在微搜白名单之后重新加入其他工具。
        self.tool_providers = []

    async def preprocess(self, req: Dict[str, Any], state: AgentRunState) -> None:
        if not str(req.get("tenantId") or "").strip() or not str(
            req.get("userId") or ""
        ).strip():
            raise ValueError("VoiceAgent 请求缺少 tenantId 或 userId")

        alg_extra = req.setdefault("algExtra", {})
        if not isinstance(alg_extra, dict):
            raise ValueError("VoiceAgent 请求的 algExtra 必须是对象")
        search_range = alg_extra.setdefault("searchRange", {})
        if not isinstance(search_range, dict):
            raise ValueError("VoiceAgent 请求的 algExtra.searchRange 必须是对象")

        # 能力由 VoiceAgent 固定收紧，不能由请求打开其他检索工具。
        search_range.update(
            {"innerCorpus": 0, "network": 0, "weisou": 1, "db": 0}
        )
        req["_disabled_retrieval_tool_names"] = []
        req["max_tool_rounds"] = 4
        # 禁止 BaseToolAgent 从历史记录恢复其他 MCP，保证单工具边界不会被绕过。
        req["max_historical_tool_recoveries"] = 0

        await prepare_weisou_context(
            req=req,
            enabled=True,
            system_type_client=self.weisou_system_type_client,
            module_registry=self.weisou_module_registry,
            e9_module_registry=self.weisou_e9_module_registry,
            weaver_tenant_ids=self.weaver_tenant_ids,
            deploy_env=self.deploy_env,
            log_tag="VoiceAgent",
        )
        await super().preprocess(req, state)

    def get_system_prompt(
        self,
        req: Dict[str, Any],
        state: AgentRunState,
    ) -> PromptSpec:
        allowed_modules = list(req.get("_weisou_allowed_modules") or [])
        module_text = "、".join(allowed_modules) if allowed_modules else "当前不可用"
        prompt = self.system_prompt.replace("$_current_datetime_$", state.cur_datetime)
        prompt = prompt.replace("$_allowed_weisou_modules_$", module_text)
        return PromptSpec(
            prompt=prompt,
            template_name="voice_agent",
            prompt_vars={"allowed_weisou_modules": allowed_modules},
        )

    def get_tools(
        self,
        req: Dict[str, Any],
        state: AgentRunState,
    ) -> List[Dict[str, Any]]:
        req["_local_tool_names"] = []
        return list(state.tool_schemas)

    async def discover_tools(self, req: Dict[str, Any], state: AgentRunState) -> None:
        search_req = dict(req)
        search_req["_safe_name_assignment"] = state.safe_name_assignment
        search_req["_force_retrieval_mcp_injection"] = True
        search_req["openai_tool_schemas"] = []
        search_req["tool2mcp"] = {}
        await self.mcp_search.inject_retrieval_tools_only(search_req)

        registry = {
            name: ToolMeta.from_mapping(meta)
            for name, meta in (search_req.get("tool2mcp") or {}).items()
        }
        schemas, registry = self._keep_weisou_tools_only(
            search_req,
            list(search_req.get("openai_tool_schemas") or []),
            registry,
        )
        schemas = self._constrain_weisou_schemas(req, schemas, registry)

        state.tool_schemas = schemas
        state.tool_registry = registry
        state.safe_name_assignment = dict(
            search_req.get("_safe_name_assignment") or state.safe_name_assignment
        )
        req["_safe_name_assignment"] = dict(state.safe_name_assignment)
        req["openai_tool_schemas"] = schemas
        req["tool2mcp"] = {
            name: meta.__dict__ for name, meta in registry.items()
        }
        req["tool_name_map"] = dict(search_req.get("tool_name_map") or {})
        req["loaded_mcp_ids"] = list(
            dict.fromkeys(str(meta.mcp_id) for meta in registry.values() if meta.mcp_id)
        )
        req["mcp_discovery_path"] = "voice_weisou_only"
        logger.info(
            "VoiceAgent 只读工具加载完成 | count=%s names=%s",
            len(registry),
            list(registry),
        )

    @classmethod
    def _keep_weisou_tools_only(
        cls,
        req: Dict[str, Any],
        schemas: List[Dict[str, Any]],
        registry: Dict[str, ToolMeta],
    ) -> tuple[List[Dict[str, Any]], Dict[str, ToolMeta]]:
        return filter_voice_tools(req, schemas, registry)

    @staticmethod
    def _constrain_weisou_schemas(
        req: Dict[str, Any],
        schemas: List[Dict[str, Any]],
        registry: Dict[str, ToolMeta],
    ) -> List[Dict[str, Any]]:
        allowed_modules = list(req.get("_weisou_allowed_modules") or [])
        if not allowed_modules:
            registry.clear()
            return []

        constrained: List[Dict[str, Any]] = []
        for schema in schemas:
            safe_name = str((schema.get("function") or {}).get("name") or "")
            meta = registry.get(safe_name)
            if meta is None or meta.raw_name != "search_weisou":
                constrained.append(schema)
                continue
            updated = copy.deepcopy(schema)
            function = updated.setdefault("function", {})
            parameters = function.setdefault("parameters", {"type": "object"})
            properties = parameters.setdefault("properties", {})
            properties["module"] = {
                "type": "array",
                "description": "本次允许检索的微搜模块，只能从 enum 中选择。",
                "items": {"type": "string", "enum": allowed_modules},
                "minItems": 1,
                "uniqueItems": True,
            }
            required = [str(item) for item in parameters.get("required") or []]
            if "module" not in required:
                required.append("module")
            parameters["required"] = required

            search_type = req.get("_weisou_search_type")
            if search_type in {0, 1}:
                properties["search_type"] = {
                    "type": "integer",
                    "enum": [search_type],
                    "default": search_type,
                    "description": "本轮固定的微搜搜索类型：0=全文，1=标题。",
                }
            constrained.append(updated)
        return constrained

    def get_tool_request_headers(
        self,
        tool_meta: ToolMeta,
        req: Dict[str, Any],
        state: AgentRunState,
    ) -> Dict[str, str]:
        headers = super().get_tool_request_headers(tool_meta, req, state)
        if (tool_meta.raw_name or "") in self._ALLOWED_TOOL_NAMES:
            system_type = req.get("_weisou_system_type")
            if system_type in {0, 1}:
                headers["X-Weisou-System-Type"] = str(system_type)
        return headers
