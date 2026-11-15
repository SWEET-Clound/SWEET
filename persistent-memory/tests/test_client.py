import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from memory_system.client import MemoryClient
from memory_system.models import (
    BackgroundConsolidationConfirmation,
    EventRequest,
    EventSource,
    MemoryCandidate,
    MemoryLayer,
)


def test_client_background_consolidation_review() -> None:
    digest = "a" * 64
    path = "/v1/memory/consolidations/background"
    confirmation_id = "00000000-0000-0000-0000-000000000003"
    draft = {
        "statement": "交付前核对计划", "proposed_layer": "L4", "kind": "procedure_proposal",
        "subject_key": None, "valid_from": None, "valid_to": None,
        "source_event_ids": [
            "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002",
        ],
        "confidence": 0.9, "future_utility": "high", "durability": "stable",
        "affect": None, "sensitivity": "low", "poisoning_flags": [],
    }
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "PUT":
            assert json.loads(request.content) == {"enabled": True}
        elif request.method == "GET":
            assert dict(request.url.params) == {"limit": "1", "cursor": digest}
            return httpx.Response(200, json={
                "batches": [{"source_digest": digest, "drafts": [draft]}], "next_cursor": None,
            })
        elif request.method == "POST":
            assert request.headers["Idempotency-Key"] == "confirm"
            assert json.loads(request.content) == confirmation.model_dump(mode="json")
            return httpx.Response(200, json={
                "command_id": confirmation_id, "status": "completed", "affected_memory_ids": [],
            })
        return httpx.Response(204)

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as http:
        client = MemoryClient(http)
        client.set_background_consolidation(True)
        page = client.list_background_consolidation_drafts(cursor=digest, limit=1)
        batch, = page.batches
        confirmation = BackgroundConsolidationConfirmation(
            ordinal=0, draft=batch.drafts[0], source_event_id=confirmation_id
        )
        assert page.next_cursor is None
        assert client.confirm_background_consolidation(batch.source_digest, confirmation, "confirm").status == "completed"
        client.dismiss_background_consolidation_drafts(batch.source_digest)
    assert calls == [("PUT", path), ("GET", path), ("POST", f"{path}/{digest}/confirm"),
                     ("DELETE", f"{path}/{digest}")]


def test_client_reads_audit_and_recall_diagnostics() -> None:
    item_id = "00000000-0000-0000-0000-000000000001"
    now = datetime.now(UTC)

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path == "/v1/memory/audit":
            assert request.url.params["limit"] == "10"
            return httpx.Response(200, json=[{
                "audit_id": item_id, "action": "remember", "target_id": item_id,
                "created_at": now.isoformat(),
            }])
        assert request.url.path == f"/v1/memory/diagnostics/{item_id}"
        return httpx.Response(200, json={
            "diagnostics_id": item_id, "purpose": "task", "token_budget": 128,
            "candidate_count": 2, "selected_memory_ids": [item_id], "truncated": True,
            "created_at": now.isoformat(),
        })

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as http:
        client = MemoryClient(http)
        entry, = client.list_audit(limit=10)
        assert entry.created_at == now
        assert entry.action == "remember"
        assert client.get_recall_diagnostic(item_id).selected_memory_ids == [item_id]


def test_client_reads_candidate_decision_and_submits_review() -> None:
    memory_id = "00000000-0000-0000-0000-000000000001"
    other_id = "00000000-0000-0000-0000-000000000002"

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            assert request.url.path == f"/v1/memory/items/{memory_id}/conflicts/{other_id}/coexistence"
            return httpx.Response(204)
        if request.method == "GET":
            assert request.url.path == "/v1/memory/candidate-decisions"
            assert request.url.params["limit"] == "10"
            return httpx.Response(200, json=[{
                "decision_id": "decision", "event_id": "event", "candidate_ordinal": 0,
                "proposed_layer": "L3", "kind": "preference", "subject_key": None,
                "sensitivity": "low", "poisoning_flags": [], "outcome": "quarantined",
                "reason": "policy_allowed", "policy_version": "test",
                "stored_memory_id": memory_id, "created_at": datetime.now(UTC).isoformat(),
            }])
        assert request.method == "POST"
        assert request.url.path == f"/v1/memory/items/{memory_id}:review"
        assert json.loads(request.content) == {"decision": "reject"}
        return httpx.Response(200, json={"memory_id": memory_id, "status": "rejected"})

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as http:
        client = MemoryClient(http)
        candidate, = client.list_candidate_decisions(limit=10)
        assert candidate.outcome == "quarantined"
        assert candidate.stored_memory_id == memory_id
        assert client.review_memory(memory_id, "reject").status == "rejected"
        client.resolve_memory_conflict_as_coexisting(memory_id, other_id)
        with pytest.raises(ValueError):
            client.resolve_memory_conflict_as_coexisting(memory_id, "../invalid")


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
        assert request.url.params.get_list("layers") == ["L2", "L3"]
        assert request.url.params.get_list("statuses") == ["active", "archived"]
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
        exported = MemoryClient(http).iter_memories(
            page_size=1, layers=[MemoryLayer.EPISODIC, MemoryLayer.SEMANTIC],
            statuses=["active", "archived"],
        )
        assert [item.memory_id for item in exported] == [
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


@pytest.mark.parametrize(("layer", "valid"), [
    (MemoryLayer.EPISODIC, True), (MemoryLayer.EPISODIC, False),
    (MemoryLayer.PROCEDURAL, True), (MemoryLayer.PROCEDURAL, False),
])
def test_consolidation_confirmation_preserves_expiry_and_sources(layer, valid: bool) -> None:
    now = datetime.now(UTC)
    procedure = layer is MemoryLayer.PROCEDURAL
    proposal = MemoryCandidate(
        statement="周五完成项目交接", proposed_layer=layer,
        kind="event_summary", subject_key=None, valid_from=now, valid_to=None,
        expires_at=now + timedelta(days=7) if valid != procedure else None,
        source_event_ids=[
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ],
        confidence=0.9, future_utility="medium", durability="uncertain",
        affect=None, sensitivity="low", poisoning_flags=[],
    )
    confirmation_id = "00000000-0000-0000-0000-000000000003"

    def handle(request: httpx.Request) -> httpx.Response:
        assert valid
        if request.url.path == "/v1/memory/consolidations/threads":
            assert request.method == "GET"
            return httpx.Response(200, json=["thread/项目"])
        if request.url.path == "/v1/memory/consolidations/sources":
            assert request.method == "GET"
            assert request.url.params["thread_id"] == "thread/项目"
            return httpx.Response(200, json=proposal.source_event_ids)
        body = json.loads(request.content)
        if request.url.path == "/v1/memory/consolidations:preview":
            assert body in (
                {"event_ids": proposal.source_event_ids, "kind": "procedure"},
                {"thread_id": "thread/项目", "kind": "procedure"},
            )
            return httpx.Response(200, json=[proposal.model_dump(mode="json")])
        assert request.url.path == "/v1/memory/commands"
        assert request.headers["Idempotency-Key"] == "confirm-summary"
        assert body["source_event_id"] == confirmation_id
        assert body["supporting_event_ids"] == proposal.source_event_ids
        if procedure:
            assert body["action"] == "remember_procedure"
            assert "expires_at" not in body
        else:
            assert body["action"] == "remember"
            assert datetime.fromisoformat(body["expires_at"]) == proposal.expires_at
        return httpx.Response(200, json={
            "command_id": "confirmed", "status": "completed", "affected_memory_ids": [],
        })

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as http:
        client = MemoryClient(http)
        if valid:
            if procedure:
                thread, = client.discover_consolidation_threads()
                sources = client.discover_consolidation_sources(thread)
                proposal, = client.preview_consolidation(sources, kind="procedure")
                by_thread, = client.preview_consolidation(thread_id=thread, kind="procedure")
                assert by_thread == proposal
                with pytest.raises(ValueError, match="exactly one"):
                    client.preview_consolidation(sources, thread_id=thread)
            assert client.confirm_consolidation(
                proposal, confirmation_id, "confirm-summary"
            ).status == "completed"
            with pytest.raises(ValueError, match="must be distinct"):
                client.confirm_consolidation(
                    proposal, proposal.source_event_ids[0], "duplicate-confirmation"
                )
        else:
            with pytest.raises(ValueError, match="expiring L2"):
                client.confirm_consolidation(proposal, confirmation_id, "confirm-summary")
