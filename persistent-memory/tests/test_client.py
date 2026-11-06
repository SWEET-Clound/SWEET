import json
from datetime import UTC, datetime

import httpx
import pytest

from memory_system.client import MemoryClient
from memory_system.models import EventRequest, EventSource


def test_client_preserves_idempotency_key_and_surfaces_conflict() -> None:
    event = EventRequest(
        thread_id="thread", source=EventSource.USER, content="hello", occurred_at=datetime.now(UTC)
    )

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/memory/events"
        assert request.headers["Idempotency-Key"] == "stable-key"
        assert json.loads(request.content) == event.model_dump(mode="json")
        return httpx.Response(409, json={"code": "idempotency_conflict"})

    with (
        httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as http,
        pytest.raises(httpx.HTTPStatusError) as exc,
    ):
        MemoryClient(http).record_event(event, "stable-key")
    assert exc.value.response.status_code == 409


def test_client_streams_export_across_pages() -> None:
    cursors = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/memory/items"
        assert request.url.params["limit"] == "1"
        cursor = request.url.params.get("cursor")
        cursors.append(cursor)
        return httpx.Response(
            200,
            headers={"X-Next-Cursor": "first"} if cursor is None else {},
            json=[{
                "memory_id": "first" if cursor is None else "second",
                "statement": "example",
                "layer": "L3",
                "status": "active",
                "valid_from": None,
                "valid_to": None,
                "expires_at": None,
                "source_event_ids": [],
            }],
        )

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as http:
        assert [item.memory_id for item in MemoryClient(http).iter_memories(page_size=1)] == [
            "first", "second"
        ]
    assert cursors == [None, "first"]


def test_batch_import_can_resume_after_rate_limit() -> None:
    event = EventRequest(
        thread_id="thread", source=EventSource.USER, content="hello", occurred_at=datetime.now(UTC)
    )
    entries = [(event, "first"), (event, "second")]
    requests = []
    stored = set()

    def handle(request: httpx.Request) -> httpx.Response:
        key = request.headers["Idempotency-Key"]
        requests.append(key)
        if requests == ["first", "second"]:
            return httpx.Response(429, headers={"Retry-After": "60"})
        duplicate = key in stored
        stored.add(key)
        return httpx.Response(
            200 if duplicate else 201,
            json={"event_id": key, "recorded": not duplicate, "duplicate": duplicate},
        )

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as http:
        client = MemoryClient(http)
        first_attempt = client.import_events(entries)
        assert next(first_attempt).recorded
        with pytest.raises(httpx.HTTPStatusError) as error:
            next(first_attempt)
        assert error.value.response.headers["Retry-After"] == "60"

        replay = list(client.import_events(entries))
        assert [receipt.duplicate for receipt in replay] == [True, False]
    assert requests == ["first", "second", "first", "second"]
