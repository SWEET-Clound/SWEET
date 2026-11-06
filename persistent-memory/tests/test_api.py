from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from memory_system.api import create_app
from memory_system.models import (
    CommandReceipt,
    Identity,
    MemoryLayer,
    MemorySummary,
    ProfileProjection,
    RecordReceipt,
)
from memory_system.postgres import (
    IdempotencyConflictError,
    NotFoundError,
    RateLimitExceeded,
)
from memory_system.system import MemoryPolicyRejected


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
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


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
async def test_memory_list_returns_validated_next_cursor() -> None:
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
                )
                for number in (3, 2, 1)
            ]

    app = create_app(PagingSystem(), authenticate, lifespan)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/v1/memory/items?limit=2")
        invalid = await client.get("/v1/memory/items?cursor=not-a-uuid")

    assert page.status_code == 200
    assert len(page.json()) == 2
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

