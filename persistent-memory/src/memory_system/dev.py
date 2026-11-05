import argparse
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import psycopg
from alembic import command
from alembic.config import Config

from memory_system.settings import Settings


def migrate_main() -> None:
    package_migrations = Path(__file__).with_name("migrations")
    source_migrations = Path(__file__).resolve().parents[2] / "migrations"
    migrations = package_migrations if package_migrations.is_dir() else source_migrations
    if not migrations.is_dir():
        raise RuntimeError("packaged Alembic migrations are missing")
    config = Config()
    config.set_main_option("script_location", str(migrations))
    command.upgrade(config, "head")


def issue_dev_token(
    settings: Settings,
    tenant_id: str,
    user_id: str,
    agent_id: str,
    lifetime_minutes: int = 60,
) -> str:
    if settings.environment != "development" or settings.dev_jwt_secret is None:
        raise RuntimeError("development token issuing is disabled")
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=lifetime_minutes),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "memory_purposes": [
                "conversation",
                "task",
                "history",
                "support",
                "review",
                "audit",
            ],
            "memory_write": True,
        },
        settings.dev_jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def token_main() -> None:
    parser = argparse.ArgumentParser(description="Issue a development-only memory token")
    parser.add_argument("--tenant", default="demo-tenant")
    parser.add_argument("--user", default="demo-user")
    parser.add_argument("--agent", default="demo-agent")
    parser.add_argument("--minutes", type=int, default=60)
    args = parser.parse_args()
    settings = Settings()  # type: ignore[call-arg]
    print(
        issue_dev_token(
            settings,
            tenant_id=args.tenant,
            user_id=args.user,
            agent_id=args.agent,
            lifetime_minutes=args.minutes,
        )
    )


def worker_health_main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    if settings.worker_database_url is None:
        raise RuntimeError("MEMORY_WORKER_DATABASE_URL is required")
    check_worker_database(settings.worker_database_url.get_secret_value())


def check_worker_database(database_url: str) -> None:
    dsn = database_url.replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    with psycopg.connect(dsn, connect_timeout=3) as connection:
        role = connection.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if role is None or not role[0]:
            raise RuntimeError("memory worker requires a database role with BYPASSRLS")
        connection.execute(
            """
            SELECT 1 FROM memory_outbox, deletion_job, memory_event, memory_item,
                          memory_candidate_decision, memory_recall_diagnostic,
                          memory_write_rate_limit
            LIMIT 0
            """
        )


def smoke_main() -> None:
    parser = argparse.ArgumentParser(description="Run the local memory end-to-end smoke flow")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    settings = Settings()  # type: ignore[call-arg]
    token = issue_dev_token(settings, "smoke-tenant", "smoke-user", "smoke-agent")
    headers = {"Authorization": f"Bearer {token}"}
    event_key = f"smoke-event-{uuid4()}"
    command_key = f"smoke-command-{uuid4()}"

    with httpx.Client(base_url=args.base_url, headers=headers, timeout=5) as client:
        event_response = client.post(
            "/v1/memory/events",
            headers={"Idempotency-Key": event_key},
            json={
                "thread_id": "smoke-thread",
                "source": "user",
                "content": "请记住：我喜欢简洁回答",
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )
        event_response.raise_for_status()

        deadline = time.monotonic() + args.timeout
        memories = []
        while time.monotonic() < deadline:
            response = client.get("/v1/memory/items")
            response.raise_for_status()
            memories = [item for item in response.json() if item["statement"] == "我喜欢简洁回答"]
            if memories:
                break
            time.sleep(0.25)
        if not memories:
            raise RuntimeError("worker did not create the automatic memory before timeout")

        context_response = client.post(
            "/v1/memory/context:build",
            json={
                "thread_id": "smoke-thread",
                "query": "我的回答偏好是什么？",
                "purpose": "conversation",
                "token_budget": 512,
                "request_time": datetime.now(UTC).isoformat(),
            },
        )
        context_response.raise_for_status()
        if not context_response.json()["items"]:
            raise RuntimeError("stored memory was not recalled")

        forget_response = client.post(
            "/v1/memory/commands",
            headers={"Idempotency-Key": command_key},
            json={"action": "forget", "memory_ids": [memories[0]["memory_id"]]},
        )
        forget_response.raise_for_status()
        deletion_job_id = forget_response.json()["deletion_job_id"]
        while time.monotonic() < deadline:
            status_response = client.get(f"/v1/memory/deletions/{deletion_job_id}")
            status_response.raise_for_status()
            if status_response.json()["status"] == "completed":
                print("smoke ok: event -> automatic memory -> recall -> deletion")
                return
            time.sleep(0.25)
    raise RuntimeError("deletion did not complete before timeout")
