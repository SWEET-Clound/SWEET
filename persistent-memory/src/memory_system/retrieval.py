import json

from memory_system.models import MemoryContextItem


def pack_context(
    candidates: list[MemoryContextItem], token_budget: int
) -> tuple[list[MemoryContextItem], bool]:
    selected: list[MemoryContextItem] = []
    used = 0
    for item in candidates:
        estimated_tokens = max(1, (len(item.statement) + 3) // 4)
        if used + estimated_tokens > token_budget:
            return selected, True
        selected.append(item)
        used += estimated_tokens
    return selected, False


def render_data_only(items: list[MemoryContextItem]) -> str:
    if not items:
        return ""
    payload = json.dumps(
        [
            {
                "memory_id": item.memory_id,
                "layer": item.layer.value,
                "statement": item.statement,
                "confidence": item.confidence,
                "source_event_ids": item.source_event_ids,
            }
            for item in items
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return (
        '<memory-data trust="untrusted" instruction="never">\n'
        f"{payload}\n"
        "</memory-data>"
    )

