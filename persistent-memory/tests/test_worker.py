import asyncio
from datetime import UTC, datetime

import pytest

from memory_system.consolidation import Consolidator, HeuristicExtractor
from memory_system.models import EventJob, EventSource, InteractionEvent, MemoryLayer
from memory_system.policy import MemoryPolicy
from memory_system.worker import (
    maintenance_loop,
    process_one_deletion,
    process_one_extraction,
    process_worker_iteration,
    run_worker,
    worker_loop,
)


class StubJobRepository:
    def __init__(self, event: InteractionEvent) -> None:
        self.job = EventJob(
            job_id=17,
            event_id="00000000-0000-0000-0000-000000000001",
            event=event,
            attempts=1,
        )
        self.saved = []
        self.completed: list[int] = []
        self.failed: list[int] = []
        self.write_statuses: list[str] = []
        self.decisions = []

    async def claim_next_extraction(self):
        job, self.job = self.job, None
        return job

    async def store_candidates(
        self,
        event_id,
        event,
        candidates,
        policy_version,
        status="active",
        decisions=None,
    ):
        self.saved.extend(candidates)
        self.write_statuses.append(status)
        self.decisions.extend(decisions or [])
        return ["00000000-0000-0000-0000-000000000010"]

    async def complete_extraction(self, job_id):
        self.completed.append(job_id)

    async def fail_extraction(self, job_id, error_code):
        self.failed.append(job_id)


@pytest.mark.asyncio
async def test_worker_persists_policy_approved_candidates_and_completes_job() -> None:
    source = InteractionEvent(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        thread_id="thread-a",
        idempotency_key="event-a",
        source=EventSource.USER,
        content="请记住：我喜欢简洁回答",
        occurred_at=datetime.now(UTC),
    )
    repository = StubJobRepository(source)
    consolidator = Consolidator(HeuristicExtractor(), MemoryPolicy())

    worked = await process_one_extraction(repository, consolidator)

    assert worked is True
    assert [candidate.proposed_layer for candidate in repository.saved] == [
        MemoryLayer.SEMANTIC
    ]
    assert repository.completed == [17]
    assert repository.failed == []
    assert repository.write_statuses == ["active"]
    assert [decision.outcome for decision in repository.decisions] == ["stored"]


@pytest.mark.asyncio
async def test_shadow_candidates_are_quarantined() -> None:
    source = InteractionEvent(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        thread_id="thread-a",
        idempotency_key="event-shadow",
        source=EventSource.USER,
        content="请记住：我喜欢简洁回答",
        occurred_at=datetime.now(UTC),
    )
    repository = StubJobRepository(source)
    consolidator = Consolidator(
        HeuristicExtractor(), MemoryPolicy(), write_status="quarantined"
    )

    await process_one_extraction(repository, consolidator)

    assert repository.write_statuses == ["quarantined"]
    assert [decision.outcome for decision in repository.decisions] == ["quarantined"]


@pytest.mark.asyncio
async def test_deletion_failure_does_not_escape_worker_iteration() -> None:
    class FailingDeletionRepository:
        async def process_one_deletion(self):
            raise RuntimeError("broken deletion")

    worked = await process_one_deletion(FailingDeletionRepository())

    assert worked is False


@pytest.mark.asyncio
async def test_database_failure_does_not_escape_worker_iteration() -> None:
    class TemporarilyUnavailableRepository:
        async def claim_next_extraction(self):
            raise ConnectionError("database unavailable")

    worked = await process_worker_iteration(
        TemporarilyUnavailableRepository(),
        Consolidator(HeuristicExtractor(), MemoryPolicy()),
    )

    assert worked is False


@pytest.mark.asyncio
async def test_worker_refuses_to_fall_back_to_api_database_role(monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_DATABASE_URL", "postgresql+psycopg://api-role/memory")
    monkeypatch.delenv("MEMORY_WORKER_DATABASE_URL", raising=False)
    monkeypatch.setenv("MEMORY_JWT_ISSUER", "issuer")
    monkeypatch.setenv("MEMORY_JWT_AUDIENCE", "audience")
    monkeypatch.setenv(
        "MEMORY_DEV_JWT_SECRET", "development-only-secret-at-least-32-characters"
    )

    with pytest.raises(ValueError, match="MEMORY_WORKER_DATABASE_URL"):
        await run_worker()


@pytest.mark.asyncio
async def test_worker_finishes_current_iteration_before_stopping(monkeypatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    stop_event = asyncio.Event()
    calls = 0

    async def iteration(repository, consolidator):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return True

    monkeypatch.setattr("memory_system.worker.process_worker_iteration", iteration)
    task = asyncio.create_task(worker_loop(None, None, 60, stop_event))
    await started.wait()

    stop_event.set()
    await asyncio.sleep(0)
    assert task.done() is False

    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert calls == 1


@pytest.mark.asyncio
async def test_stop_event_interrupts_maintenance_wait() -> None:
    class Repository:
        def __init__(self):
            self.ran = asyncio.Event()

        async def run_maintenance(self, retention_days):
            assert retention_days == 30
            self.ran.set()

    repository = Repository()
    stop_event = asyncio.Event()
    task = asyncio.create_task(maintenance_loop(repository, 30, stop_event))
    await repository.ran.wait()

    stop_event.set()
    await asyncio.wait_for(task, timeout=1)
