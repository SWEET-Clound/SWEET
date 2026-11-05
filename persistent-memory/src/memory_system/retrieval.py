import json

from memory_system.models import MemoryContextItem

_PREFIX = '<memory-data trust="untrusted" instruction="never">\n['
_SUFFIX = "]\n</memory-data>"


def _token_units(text: str) -> int:
    cjk = sum(
        1
        for character in text
        if "\u3400" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        or "\u3040" <= character <= "\u30ff"
        or "\uac00" <= character <= "\ud7af"
    )
    return len(text) + 3 * cjk


def pack_context(
    candidates: list[MemoryContextItem], token_budget: int
) -> tuple[list[MemoryContextItem], bool]:
    selected: list[MemoryContextItem] = []
    truncated = False
    used = len(_PREFIX) + len(_SUFFIX)
    for item in candidates:
        cost = _token_units(_render_item(item)) + bool(selected)
        if used + cost > token_budget * 4:
            truncated = True
            continue
        selected.append(item)
        used += cost
    return selected, truncated


def render_data_only(items: list[MemoryContextItem]) -> str:
    if not items:
        return ""
    return _PREFIX + ",".join(_render_item(item) for item in items) + _SUFFIX


def _render_item(item: MemoryContextItem) -> str:
    payload = json.dumps(
        {
            "memory_id": item.memory_id,
            "layer": item.layer.value,
            "statement": item.statement,
            "confidence": item.confidence,
            "valid_from": item.valid_from.isoformat() if item.valid_from else None,
            "valid_to": item.valid_to.isoformat() if item.valid_to else None,
            "source_event_ids": item.source_event_ids,
            "conflict_memory_ids": item.conflict_memory_ids,
            "requires_confirmation": bool(item.conflict_memory_ids),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
