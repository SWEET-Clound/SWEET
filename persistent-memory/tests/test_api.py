from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from memory_system.api import create_app
from memory_system.models import Identity, RecordReceipt


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

