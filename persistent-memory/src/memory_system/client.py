"""Typed HTTP client for the core memory lifecycle."""

from collections.abc import Iterable, Iterator

import httpx

from memory_system.models import (
    CommandReceipt,
    CommandRequest,
    DeletionStatus,
    EventRequest,
    MemoryContext,
    MemorySummary,
    RecallRequestBody,
    RecordReceipt,
)


class MemoryClient:
    """Use a caller-owned HTTP client configured with a base URL and credentials."""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http

    def record_event(self, event: EventRequest, idempotency_key: str) -> RecordReceipt:
        response = self.http.post(
            "/v1/memory/events",
            json=event.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key},
        )
        return RecordReceipt.model_validate(response.raise_for_status().json())

    def import_events(
        self, events: Iterable[tuple[EventRequest, str]]
    ) -> Iterator[RecordReceipt]:
        """Replay events individually with caller-supplied stable idempotency keys."""
        for event, key in events:
            yield self.record_event(event, key)

    def iter_memories(self, *, page_size: int = 100) -> Iterator[MemorySummary]:
        """Stream active memory summaries visible to the configured identity."""
        params: dict[str, str | int] = {"limit": page_size}
        seen: set[str] = set()
        while True:
            response = self.http.get("/v1/memory/items", params=params).raise_for_status()
            for item in response.json():
                yield MemorySummary.model_validate(item)
            cursor = response.headers.get("X-Next-Cursor")
            if not cursor:
                return
            if cursor in seen:
                raise RuntimeError("memory pagination repeated a cursor")
            seen.add(cursor)
            params["cursor"] = cursor

    def build_context(self, request: RecallRequestBody) -> MemoryContext:
        response = self.http.post("/v1/memory/context:build", json=request.model_dump(mode="json"))
        return MemoryContext.model_validate(response.raise_for_status().json())

    def execute_command(self, command: CommandRequest, idempotency_key: str) -> CommandReceipt:
        response = self.http.post(
            "/v1/memory/commands",
            json=command.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key},
        )
        return CommandReceipt.model_validate(response.raise_for_status().json())

    def deletion_status(self, job_id: str) -> DeletionStatus:
        response = self.http.get(f"/v1/memory/deletions/{job_id}")
        return DeletionStatus.model_validate(response.raise_for_status().json())
