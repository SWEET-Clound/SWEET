"""VoiceAgent 第一版的固定工具权限策略。"""

from collections.abc import Mapping
from typing import Any


ALLOWED_VOICE_TOOL_NAMES = frozenset(
    {"search_weisou", "read_weisou_document"}
)


def filter_voice_tools(
    req: Mapping[str, Any],
    schemas: list[dict[str, Any]],
    registry: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """同时过滤模型可见 schema 和执行注册表，未知工具默认拒绝。"""
    tool_name_map = req.get("tool_name_map") or {}
    raw_safe_to_raw = (
        tool_name_map.get("safe_to_raw") or {}
        if isinstance(tool_name_map, Mapping)
        else {}
    )
    safe_to_raw = raw_safe_to_raw if isinstance(raw_safe_to_raw, Mapping) else {}

    def allowed(safe_name: str) -> bool:
        meta = registry.get(safe_name)
        if meta is None:
            return False
        raw_name = str(
            getattr(meta, "raw_name", "")
            or safe_to_raw.get(safe_name)
            or safe_name
        )
        return raw_name in ALLOWED_VOICE_TOOL_NAMES

    filtered_registry = {
        name: meta for name, meta in registry.items() if allowed(name)
    }
    filtered_schemas = [
        schema
        for schema in schemas
        if allowed(str((schema.get("function") or {}).get("name") or ""))
    ]
    return filtered_schemas, filtered_registry
