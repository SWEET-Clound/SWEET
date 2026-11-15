import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY
from sqlalchemy.exc import SQLAlchemyError

from memory_system.api import create_app
from memory_system.llm import OpenAICompatibleExtractor
from memory_system.models import (
    AffectSignal,
    CommandReceipt,
    EventSource,
    Identity,
    InteractionEvent,
    MemoryLayer,
    MemorySummary,
    ProfileProjection,
    RecordReceipt,
)
from memory_system.policy import MemoryPolicy
from memory_system.postgres import (
    EventPayloadUnreadable,
    IdempotencyConflictError,
    NotFoundError,
    RateLimitExceeded,
)
from memory_system.system import MemoryPolicyRejected, MemorySystem


class StubSystem:
    repository = None

    async def record_event(self, event):
        duplicate = event.idempotency_key == "duplicate-key"
        return RecordReceipt(
            event_id="00000000-0000-0000-0000-000000000001",
            recorded=not duplicate,
            duplicate=duplicate,
        )


async def authenticate() -> Identity:
    return Identity(
        tenant_id="tenant-from-token",
        user_id="user-from-token",
        agent_id="agent-from-token",
        allowed_purposes={"conversation"},
        memory_write=True,
    )


async def authenticate_auditor() -> Identity:
    return Identity(
        tenant_id="tenant-from-token",
        user_id="user-from-token",
        agent_id="agent-from-token",
        allowed_purposes={"conversation", "audit"},
    )


async def authenticate_reviewer() -> Identity:
    return Identity(
        tenant_id="tenant-from-token",
        user_id="user-from-token",
        agent_id="agent-from-token",
        allowed_purposes={"review"},
        memory_write=True,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


@pytest.mark.asyncio
async def test_affect_history_requires_permitted_purpose() -> None:
    class HistorySystem(StubSystem):
        async def affect_history(self, scope, memory_id, limit, as_of):
            assert scope.user_id == "user-from-token"
            assert limit == 10
            assert as_of == datetime(2026, 11, 10, tzinfo=UTC)
            return []

    async def historian():
        return (await authenticate()).model_copy(update={"allowed_purposes": {"history"}})

    path = "/v1/memory/items/00000000-0000-0000-0000-000000000001/affect-history?limit=10"
    path += "&as_of=2026-11-10T00:00:00Z"
    for auth, expected in ((authenticate, 403), (historian, 200)):
        app = create_app(HistorySystem(), auth, lifespan)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(path)
        assert response.status_code == expected


@pytest.mark.asyncio
async def test_http_latency_counts_failed_handlers_without_raw_paths() -> None:
    class BrokenSystem(StubSystem):
        async def record_event(self, event):
            raise RuntimeError("simulated failure")

    labels = {"path": "/v1/memory/events"}
    before = REGISTRY.get_sample_value("memory_http_request_duration_seconds_count", labels) or 0
    app = create_app(BrokenSystem(), authenticate, lifespan)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        failed = await client.post(
            "/v1/memory/events", headers={"Idempotency-Key": "failure"},
            json={"thread_id": "thread", "source": "user", "content": "hello",
                  "occurred_at": datetime.now(UTC).isoformat()},
        )
        missing = await client.get("/private-user-path-for-latency-test")
        unknown_method = await client.request("UNRECOGNIZED-METHOD", "/v1/memory/events")
        metrics = await client.get("/metrics")
    assert failed.status_code == 500
    assert missing.status_code == 404
    assert unknown_method.status_code == 405
    assert REGISTRY.get_sample_value(
        "memory_http_request_duration_seconds_count", labels
    ) == before + 2
    assert "private-user-path-for-latency-test" not in metrics.text
    assert "UNRECOGNIZED-METHOD" not in metrics.text
    assert 'method="OTHER"' in metrics.text
    assert 'memory_http_request_duration_seconds_count{path="unmatched"}' in metrics.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["source_event_id", "supporting_event_ids"])
async def test_remember_rejects_invalid_event_ids_before_storage(field) -> None:
    app = create_app(StubSystem(), authenticate, lifespan)
    body = {
        "action": "remember", "statement": "confirmed",
        "source_event_id": "00000000-0000-0000-0000-000000000001",
    }
    body[field] = ["invalid-event"] if field == "supporting_event_ids" else "invalid-event"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/memory/commands", json=body, headers={"Idempotency-Key": "invalid-event"}
        )
    assert response.status_code == 422
    assert "invalid-event" not in response.text


@pytest.mark.asyncio
async def test_empty_supporting_events_preserve_legacy_command_hash() -> None:
    body = {
        "action": "remember", "statement": "confirmed",
        "source_event_id": "00000000-0000-0000-0000-000000000001",
    }
    expected_hash = hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()
    calls = []

    class CommandSystem(StubSystem):
        async def execute_command_idempotent(self, command, key, payload_hash):
            calls.append(payload_hash)
            return CommandReceipt(command_id="receipt", status="completed", affected_memory_ids=[])

    app = create_app(CommandSystem(), authenticate, lifespan)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for payload in (body, body | {"supporting_event_ids": [], "expires_at": None}):
            response = await client.post(
                "/v1/memory/commands", json=payload, headers={"Idempotency-Key": "legacy"}
            )
            assert response.status_code == 200
    assert calls == [expected_hash, expected_hash]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/memory/events", {
            "thread_id": "thread-a", "source": "user", "content": "hello",
            "occurred_at": datetime.now(UTC).isoformat(),
        }),
        ("/v1/memory/commands", {
            "action": "remember", "statement": "confirmed",
            "source_event_id": "00000000-0000-0000-0000-000000000001",
        }),
        ("/v1/memory/items/00000000-0000-0000-0000-000000000001:review", {
            "decision": "approve",
        }),
    ],
)
async def test_read_only_identity_cannot_mutate_memory(path, body) -> None:
    async def read_only():
        return (await authenticate_reviewer()).model_copy(update={"memory_write": False})

    class ReadableSystem(StubSystem):
        async def record_event(self, event):
            raise AssertionError("read-only request reached mutation")

        async def list_memories(self, query):
            return []

    app = create_app(ReadableSystem(), read_only, lifespan)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(path, json=body, headers={"Idempotency-Key": "read-only"})
        readable = await client.get("/v1/memory/items")
    assert denied.status_code == 403
    assert denied.json() == {"detail": "memory_write_not_allowed"}
    assert readable.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth", "ids", "expected"),
    [
        (authenticate, ["00000000-0000-0000-0000-000000000001",
                        "00000000-0000-0000-0000-000000000002"], 403),
        (authenticate_reviewer, ["00000000-0000-0000-0000-000000000001"] * 2, 422),
    ],
)
async def test_consolidation_preview_checks_review_and_distinct_sources(auth, ids, expected) -> None:
    app = create_app(StubSystem(), auth, lifespan)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/memory/consolidations:preview", json={"event_ids": ids})
    assert response.status_code == expected


@pytest.mark.asyncio
async def test_consolidation_preview_returns_503_on_oversized_model_response(monkeypatch) -> None:
    monkeypatch.setattr("memory_system.llm._MAX_RESPONSE_BYTES", 16)
    ids = [str(UUID(int=1)), str(UUID(int=2))]
    attempts = []

    class Repository:
        async def consolidation_events(self, scope, event_ids):
            return [
                (event_id, InteractionEvent(
                    **scope.model_dump(), thread_id="thread-a", idempotency_key=event_id,
                    source=EventSource.USER, content="项目交付记录", occurred_at=datetime.now(UTC),
                ))
                for event_id in event_ids
            ]

        async def consume_consolidation_quota(self, scope, limit):
            pass

    def oversized(request):
        attempts.append(request)
        return httpx.Response(200, content=b"x" * 17)

    with httpx.Client(transport=httpx.MockTransport(oversized)) as model_client:
        extractor = OpenAICompatibleExtractor(
            "http://model.test/v1", "summarizer", None, client=model_client
        )
        system = MemorySystem(Repository(), MemoryPolicy(), 2048, consolidation_extractor=extractor)
        app = create_app(system, authenticate_reviewer, lifespan)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/memory/consolidations:preview", json={"event_ids": ids})
    assert response.status_code == 503
    assert response.json() == {"code": "consolidation_unavailable"}
    assert len(attempts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2])
async def test_consolidation_preview_selects_thread_sources(count) -> None:
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(count)]
    calls = []

    class ThreadSystem(StubSystem):
        @property
        def repository(self):
            return self

        async def discover_consolidation_sources(self, scope, thread_id):
            assert thread_id == "thread-a"
            calls.append("discover")
            return ids

        async def propose_consolidation(self, scope, event_ids, *, kind, automatic_sources):
            assert event_ids == ids
            assert kind == "procedure"
            assert automatic_sources
            calls.append("preview")
            return []

    app = create_app(ThreadSystem(), authenticate_reviewer, lifespan)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/memory/consolidations:preview",
            json={"thread_id": "thread-a", "kind": "procedure"},
        )
        ambiguous = await client.post(
            "/v1/memory/consolidations:preview",
            json={"thread_id": "thread-a", "event_ids": ids},
        )
    assert response.status_code == 200
    assert response.json() == []
    assert ambiguous.status_code == 422
    assert calls == (["discover", "preview"] if count == 2 else ["discover"])


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["sources?thread_id=thread-a", "threads"])
async def test_consolidation_source_discovery_requires_review(path) -> None:
    app = create_app(StubSystem(), authenticate, lifespan)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/memory/consolidations/{path}")
    assert response.status_code == 403
    assert response.json() == {"detail": "purpose_not_allowed"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("review", "write", "enabled", "status"),
    [(False, True, True, 403), (True, False, True, 403),
     (True, True, "true", 422), (True, True, True, 204), (True, False, False, 204)],
)
async def test_background_consolidation_consent(review, write, enabled, status) -> None:
    calls = []
    identity = Identity(
        tenant_id="tenant-a", user_id="user-a", agent_id="agent-a",
        allowed_purposes={"review"} if review else set(), memory_write=write,
    )

    def authenticate_scope():
        return identity

    class ConsentSystem(StubSystem):
        @property
        def repository(self):
            return self

        async def set_background_consolidation(self, scope, enabled):
            calls.append((scope, enabled))

    app = create_app(ConsentSystem(), authenticate_scope, lifespan)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/v1/memory/consolidations/background", json={"enabled": enabled}
        )
    assert response.status_code == status
    assert calls == ([(identity.scope(), enabled)] if status == 204 else [])


@pytest.mark.asyncio
async def test_event_endpoint_distinguishes_created_from_idempotent_replay() -> None:
    app = create_app(StubSystem(), authenticate, lifespan)
    body = {
        "thread_id": "thread-a",
        "source": "user",
        "content": "请记住我喜欢简洁回答",
        "occurred_at": datetime.now(UTC).isoformat(),
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/memory/events", json=body, headers={"Idempotency-Key": "first-key"}
        )
        duplicate = await client.post(
            "/v1/memory/events", json=body, headers={"Idempotency-Key": "duplicate-key"}
        )
        metrics = await client.get("/metrics")

    assert created.status_code == 201
    assert duplicate.status_code == 200
    assert created.headers["X-Memory-Diagnostics-Id"]
    assert duplicate.json()["duplicate"] is True
    assert metrics.status_code == 200
    assert "memory_http_requests_total" in metrics.text


@pytest.mark.asyncio
async def test_event_endpoint_rejects_naive_time() -> None:
    app = create_app(StubSystem(), authenticate, lifespan)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/memory/events",
            headers={"Idempotency-Key": "naive-time"},
            json={
                "thread_id": "thread-a",
                "source": "user",
                "content": "hello",
                "occurred_at": "2026-11-06T12:00:00",
            },
        )
    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"loc": ["body", "occurred_at"], "type": "timezone_aware"}
    ]


@pytest.mark.asyncio
async def test_event_endpoint_rejects_oversized_metadata() -> None:
    app = create_app(StubSystem(), authenticate, lifespan)
    body = {
        "thread_id": "thread-a",
        "source": "user",
        "content": "hello",
        "occurred_at": datetime.now(UTC).isoformat(),
        "metadata": {"untrusted": "x" * 32_769},
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/memory/events", json=body, headers={"Idempotency-Key": "large-metadata"}
        )

    assert response.status_code == 422
    assert "untrusted" not in response.text
    assert "xxxxxxxxxx" not in response.text


@pytest.mark.asyncio
async def test_write_rate_limit_returns_retryable_429() -> None:
    class LimitedSystem(StubSystem):
        async def record_event(self, event):
            raise RateLimitExceeded("event", retry_after_seconds=17)

    app = create_app(LimitedSystem(), authenticate, lifespan)
    body = {
        "thread_id": "thread-a",
        "source": "user",
        "content": "hello",
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/memory/events",
            json=body,
            headers={"Idempotency-Key": "rate-limited"},
        )
        metrics = await client.get("/metrics")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "17"
    assert response.json() == {"code": "write_rate_limited", "operation": "event"}
    assert 'memory_rate_limited_writes_total{operation="event"}' in metrics.text


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_status", ["quarantined", "rejected"])
async def test_candidate_listing_requires_review_purpose(candidate_status: str) -> None:
    class CandidateSystem(StubSystem):
        async def list_memories(self, query):
            assert query.statuses == ["active", candidate_status]
            return []

    for auth, expected in ((authenticate, 403), (authenticate_reviewer, 200)):
        app = create_app(CandidateSystem(), auth, lifespan)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/v1/memory/items",
                params=[("statuses", "active"), ("statuses", candidate_status)],
            )
        assert response.status_code == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("can_read_affect", [False, True])
async def test_memory_list_returns_validated_next_cursor(can_read_affect) -> None:
    class PagingSystem(StubSystem):
        async def list_memories(self, query):
            assert query.limit == 3
            return [
                MemorySummary(
                    memory_id=f"00000000-0000-0000-0000-{number:012d}",
                    statement=f"memory {number}",
                    layer=MemoryLayer.SEMANTIC,
                    status="active",
                    valid_from=None,
                    valid_to=None,
                    expires_at=None,
                    source_event_ids=[],
                    affect=AffectSignal(
                        valence=-0.5, arousal=0.7, labels=["distress"], confidence=0.9,
                        observed_at=datetime.now(UTC), derived_from="text",
                    ),
                )
                for number in (3, 2, 1)
            ]

    async def authenticate_list():
        identity = await authenticate()
        if can_read_affect:
            identity.allowed_purposes.add("support")
        return identity

    app = create_app(PagingSystem(), authenticate_list, lifespan)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/v1/memory/items?limit=2")
        invalid = await client.get("/v1/memory/items?cursor=not-a-uuid")

    assert page.status_code == 200
    assert len(page.json()) == 2
    assert all((item["affect"] is not None) == can_read_affect for item in page.json())
    assert page.headers["X-Next-Cursor"] == "00000000-0000-0000-0000-000000000002"
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_profile_endpoint_is_purpose_gated_and_returns_diagnostics() -> None:
    diagnostics_id = "00000000-0000-0000-0000-000000000010"

    class ProfileSystem(StubSystem):
        async def build_profile(self, scope, purpose, token_budget, now):
            assert scope.user_id == "user-from-token"
            assert purpose == "conversation"
            assert token_budget == 128
            return ProfileProjection(
                items=[],
                rendered_text="",
                truncated=False,
                diagnostics_id=diagnostics_id,
                as_of=now,
            )

    denied_app = create_app(ProfileSystem(), authenticate_reviewer, lifespan)
    allowed_app = create_app(ProfileSystem(), authenticate, lifespan)
    params = {"purpose": "conversation", "token_budget": 128}
    async with AsyncClient(
        transport=ASGITransport(app=denied_app), base_url="http://test"
    ) as client:
        denied = await client.get("/v1/memory/profile", params=params)
    async with AsyncClient(
        transport=ASGITransport(app=allowed_app), base_url="http://test"
    ) as client:
        allowed = await client.get("/v1/memory/profile", params=params)

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["items"] == []
    assert allowed.headers["X-Memory-Diagnostics-Id"] == diagnostics_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {
            "action": "remember_procedure",
            "statement": "回答代码问题时先给结论",
            "source_event_id": "00000000-0000-0000-0000-000000000001",
        },
        {
            "action": "correct_procedure",
            "memory_id": "00000000-0000-0000-0000-000000000002",
            "replacement": "回答代码问题时先给结论再给验证",
            "source_event_id": "00000000-0000-0000-0000-000000000001",
        },
    ],
)
async def test_procedure_commands_require_review_purpose(body) -> None:
    class CommandSystem(StubSystem):
        async def execute_command_idempotent(self, command, key, payload_hash):
            assert command.action == body["action"]
            assert key == "procedure-key"
            assert payload_hash
            return CommandReceipt(
                command_id="00000000-0000-0000-0000-000000000010",
                status="completed",
                affected_memory_ids=[],
            )

    denied_app = create_app(CommandSystem(), authenticate, lifespan)
    allowed_app = create_app(CommandSystem(), authenticate_reviewer, lifespan)
    headers = {"Idempotency-Key": "procedure-key"}
    async with AsyncClient(
        transport=ASGITransport(app=denied_app), base_url="http://test"
    ) as client:
        denied = await client.post("/v1/memory/commands", json=body, headers=headers)
    async with AsyncClient(
        transport=ASGITransport(app=allowed_app), base_url="http://test"
    ) as client:
        allowed = await client.post("/v1/memory/commands", json=body, headers=headers)

    assert denied.status_code == 403
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_audit_endpoint_requires_explicit_purpose() -> None:
    class AuditSystem(StubSystem):
        async def list_audit(self, scope, limit):
            assert scope.user_id == "user-from-token"
            assert limit == 10
            return []

    denied_app = create_app(AuditSystem(), authenticate, lifespan)
    allowed_app = create_app(AuditSystem(), authenticate_auditor, lifespan)
    async with AsyncClient(
        transport=ASGITransport(app=denied_app), base_url="http://test"
    ) as client:
        denied = await client.get("/v1/memory/audit?limit=10")
    async with AsyncClient(
        transport=ASGITransport(app=allowed_app), base_url="http://test"
    ) as client:
        allowed = await client.get("/v1/memory/audit?limit=10")

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json() == []


@pytest.mark.asyncio
async def test_candidate_decision_endpoint_requires_audit_purpose() -> None:
    class DecisionSystem(StubSystem):
        async def list_candidate_decisions(self, scope, limit):
            assert scope.user_id == "user-from-token"
            assert limit == 10
            return []

    denied_app = create_app(DecisionSystem(), authenticate, lifespan)
    allowed_app = create_app(DecisionSystem(), authenticate_auditor, lifespan)
    async with AsyncClient(
        transport=ASGITransport(app=denied_app), base_url="http://test"
    ) as client:
        denied = await client.get("/v1/memory/candidate-decisions?limit=10")
    async with AsyncClient(
        transport=ASGITransport(app=allowed_app), base_url="http://test"
    ) as client:
        allowed = await client.get("/v1/memory/candidate-decisions?limit=10")

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json() == []


@pytest.mark.asyncio
async def test_recall_diagnostic_endpoint_requires_audit_purpose() -> None:
    class DiagnosticSystem(StubSystem):
        async def get_recall_diagnostic(self, scope, diagnostics_id):
            return {
                "diagnostics_id": diagnostics_id,
                "purpose": "conversation",
                "token_budget": 128,
                "candidate_count": 0,
                "selected_memory_ids": [],
                "truncated": False,
                "created_at": datetime.now(UTC),
            }

    diagnostics_id = "00000000-0000-0000-0000-000000000001"
    denied_app = create_app(DiagnosticSystem(), authenticate, lifespan)
    allowed_app = create_app(DiagnosticSystem(), authenticate_auditor, lifespan)
    async with AsyncClient(
        transport=ASGITransport(app=denied_app), base_url="http://test"
    ) as client:
        denied = await client.get(f"/v1/memory/diagnostics/{diagnostics_id}")
    async with AsyncClient(
        transport=ASGITransport(app=allowed_app), base_url="http://test"
    ) as client:
        allowed = await client.get(f"/v1/memory/diagnostics/{diagnostics_id}")

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["diagnostics_id"] == diagnostics_id


@pytest.mark.asyncio
async def test_review_endpoint_requires_explicit_purpose() -> None:
    class ReviewSystem(StubSystem):
        async def review_memory(self, scope, memory_id, decision):
            return {"memory_id": memory_id, "status": "active"}

    memory_id = "00000000-0000-0000-0000-000000000001"
    denied_app = create_app(ReviewSystem(), authenticate, lifespan)
    allowed_app = create_app(ReviewSystem(), authenticate_reviewer, lifespan)
    async with AsyncClient(
        transport=ASGITransport(app=denied_app), base_url="http://test"
    ) as client:
        denied = await client.post(
            f"/v1/memory/items/{memory_id}:review", json={"decision": "approve"}
        )
    async with AsyncClient(
        transport=ASGITransport(app=allowed_app), base_url="http://test"
    ) as client:
        allowed = await client.post(
            f"/v1/memory/items/{memory_id}:review", json={"decision": "approve"}
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json() == {"memory_id": memory_id, "status": "active"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (IdempotencyConflictError(), 409, "idempotency_conflict"),
        (NotFoundError(), 403, "scope_invalid_or_forbidden"),
        (MemoryPolicyRejected("policy_blocked"), 422, "policy_blocked"),
        (SQLAlchemyError("database unavailable"), 503, "memory_unavailable"),
        (EventPayloadUnreadable("private decoder details"), 503, "memory_unavailable"),
    ],
)
async def test_public_exception_mapping(error, expected_status, expected_code) -> None:
    class FailingSystem(StubSystem):
        async def record_event(self, event):
            raise error

    app = create_app(FailingSystem(), authenticate, lifespan)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/memory/events",
            headers={"Idempotency-Key": "failing-event"},
            json={
                "thread_id": "thread-a",
                "source": "user",
                "content": "hello",
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code

