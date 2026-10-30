import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from memory_system.consolidation import Consolidator, HeuristicExtractor
from memory_system.models import (
    EventSource,
    InteractionEvent,
    MemoryListQuery,
    MemoryScope,
    RememberCommand,
)
from memory_system.policy import MemoryPolicy
from memory_system.postgres import IdempotencyConflictError, PostgresRepository
from memory_system.worker import process_one_extraction


def _test_database_url() -> str:
    url = os.getenv("MEMORY_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("MEMORY_TEST_DATABASE_URL is not configured")
    database_name = urlparse(url.replace("postgresql+psycopg", "postgresql", 1)).path.removeprefix("/")
    if not database_name.endswith("_test"):
        pytest.fail("MEMORY_TEST_DATABASE_URL must point to a disposable *_test database")
    return url


def _test_app_database_url() -> str:
    url = os.getenv("MEMORY_TEST_APP_DATABASE_URL", "")
    if not url:
        pytest.skip("MEMORY_TEST_APP_DATABASE_URL is not configured")
    database_name = urlparse(url.replace("postgresql+psycopg", "postgresql", 1)).path.removeprefix("/")
    if not database_name.endswith("_test"):
        pytest.fail("MEMORY_TEST_APP_DATABASE_URL must point to a disposable *_test database")
    return url


def _upgrade_test_database(url: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(root / "alembic.ini")
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
async def clean_postgres_test_data(request):
    if request.node.get_closest_marker("postgres") is None:
        yield
        return
    url = _test_database_url()
    _upgrade_test_database(url)
    repository = PostgresRepository.from_url(url)
    try:
        async with repository.engine.begin() as connection:
            await connection.exec_driver_sql(
                "TRUNCATE command_dedup, deletion_job, memory_audit, memory_outbox, "
                "memory_evidence, memory_item, memory_event RESTART IDENTITY CASCADE"
            )
    finally:
        await repository.close()
    yield


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_record_event_is_idempotent_and_rejects_key_reuse() -> None:
    url = _test_database_url()
    _upgrade_test_database(url)
    repository = PostgresRepository.from_url(url)
    base = InteractionEvent(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-a",
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="请记住我喜欢简洁回答",
        occurred_at=datetime.now(UTC),
    )
    try:
        first = await repository.record_event(base)
        replay = await repository.record_event(base)
        with pytest.raises(IdempotencyConflictError):
            await repository.record_event(base.model_copy(update={"content": "different"}))
    finally:
        await repository.close()

    assert first.recorded is True
    assert replay.duplicate is True
    assert replay.event_id == first.event_id


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_event_to_automatic_memory_to_physical_deletion() -> None:
    url = _test_database_url()
    _upgrade_test_database(url)
    repository = PostgresRepository.from_url(url)
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    source = InteractionEvent(
        **scope.model_dump(),
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="请记住：我喜欢简洁回答",
        occurred_at=datetime.now(UTC),
        allow_memory_extraction=True,
    )
    try:
        receipt = await repository.record_event(source)
        worked = await process_one_extraction(
            repository, Consolidator(HeuristicExtractor(), MemoryPolicy())
        )
        processing = await repository.event_processing_status(scope, receipt.event_id)
        memories = await repository.list_memories(MemoryListQuery(scope=scope))
        affected, deletion_job_id = await repository.isolate_memories(
            scope, [memories[0].memory_id]
        )
        deletion = await repository.deletion_status(scope, deletion_job_id or "")
        while deletion.status == "pending":
            assert await repository.process_one_deletion() is True
            deletion = await repository.deletion_status(scope, deletion_job_id or "")
        remaining = await repository.list_memories(MemoryListQuery(scope=scope))
        replay_after_deletion = await repository.record_event(source)
    finally:
        await repository.close()

    assert receipt.recorded is True
    assert worked is True
    assert processing.extraction_status == "completed"
    assert [memory.statement for memory in memories] == ["我喜欢简洁回答"]
    assert affected == [memories[0].memory_id]
    assert deletion.status == "completed"
    assert remaining == []
    assert replay_after_deletion.duplicate is True


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_rls_prevents_cross_user_reads_and_mutations() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    tenant = f"tenant-{uuid4()}"
    scope_a = MemoryScope(tenant_id=tenant, user_id="user-a", agent_id="agent-a")
    scope_b = MemoryScope(tenant_id=tenant, user_id="user-b", agent_id="agent-a")
    source = InteractionEvent(
        **scope_a.model_dump(),
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="请记住：A 用户喜欢简洁回答",
        occurred_at=datetime.now(UTC),
    )
    try:
        event_receipt = await repository.record_event(source)
        memory_id = await repository.remember(
            scope_a,
            "A 用户喜欢简洁回答",
            event_receipt.event_id,
            MemoryPolicy.version,
        )
        visible_to_a = await repository.list_memories(MemoryListQuery(scope=scope_a))
        visible_to_b = await repository.list_memories(MemoryListQuery(scope=scope_b))
        affected_by_b, job_created_by_b = await repository.isolate_memories(
            scope_b, [memory_id]
        )
    finally:
        await repository.close()

    assert [item.memory_id for item in visible_to_a] == [memory_id]
    assert visible_to_b == []
    assert affected_by_b == []
    assert job_created_by_b is None


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_command_effect_and_idempotency_receipt_are_one_transaction(monkeypatch) -> None:
    url = _test_database_url()
    _upgrade_test_database(url)
    repository = PostgresRepository.from_url(url)
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    source = InteractionEvent(
        **scope.model_dump(),
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="请记住：我喜欢事务完整性",
        occurred_at=datetime.now(UTC),
    )
    try:
        event = await repository.record_event(source)
        command_to_run = RememberCommand(
            action="remember",
            scope=scope,
            statement="我喜欢事务完整性",
            source_event_id=event.event_id,
        )
        original = repository._remember

        async def fail_after_effect(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("simulated process failure before receipt")

        monkeypatch.setattr(repository, "_remember", fail_after_effect)
        with pytest.raises(RuntimeError):
            await repository.execute_command_idempotent(
                command_to_run, "command-atomic", "same-payload", MemoryPolicy.version
            )
        monkeypatch.setattr(repository, "_remember", original)

        first = await repository.execute_command_idempotent(
            command_to_run, "command-atomic", "same-payload", MemoryPolicy.version
        )
        replay = await repository.execute_command_idempotent(
            command_to_run, "command-atomic", "same-payload", MemoryPolicy.version
        )
        memories = await repository.list_memories(MemoryListQuery(scope=scope))
        with pytest.raises(IdempotencyConflictError):
            await repository.execute_command_idempotent(
                command_to_run, "command-atomic", "different-payload", MemoryPolicy.version
            )
    finally:
        await repository.close()

    assert replay == first
    assert [item.statement for item in memories] == ["我喜欢事务完整性"]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_repeated_deletion_returns_existing_job_and_preserves_other_evidence() -> None:
    url = _test_database_url()
    _upgrade_test_database(url)
    repository = PostgresRepository.from_url(url)
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    sources = [
        InteractionEvent(
            **scope.model_dump(),
            thread_id="thread-a",
            idempotency_key=f"event-{uuid4()}",
            source=EventSource.USER,
            content="请记住：我喜欢简洁回答",
            occurred_at=datetime.now(UTC),
        )
        for _ in range(2)
    ]
    try:
        events = [await repository.record_event(source) for source in sources]
        memory_ids = [
            await repository.remember(
                scope, "我喜欢简洁回答", event.event_id, MemoryPolicy.version
            )
            for event in events
        ]
        affected, event_job = await repository.prohibit_events(
            scope, [events[0].event_id]
        )
        repeat_affected, repeated_job = await repository.prohibit_events(
            scope, [events[0].event_id]
        )
        await repository.process_one_deletion()
        remaining = await repository.list_memories(MemoryListQuery(scope=scope))

        forgotten, memory_job = await repository.isolate_memories(scope, memory_ids[:1])
        repeated_forgotten, repeated_memory_job = await repository.isolate_memories(
            scope, memory_ids[:1]
        )
        while (await repository.deletion_status(scope, memory_job or "")).status == "pending":
            assert await repository.process_one_deletion() is True
    finally:
        await repository.close()

    assert memory_ids[0] == memory_ids[1]
    assert affected == repeat_affected == []
    assert repeated_job == event_job
    assert [item.source_event_ids for item in remaining] == [[events[1].event_id]]
    assert repeated_forgotten == forgotten == [memory_ids[0]]
    assert repeated_memory_job == memory_job


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_prohibiting_event_cancels_pending_extraction() -> None:
    url = _test_database_url()
    _upgrade_test_database(url)
    repository = PostgresRepository.from_url(url)
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    source = InteractionEvent(
        **scope.model_dump(),
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="请记住：这条内容随后撤销",
        occurred_at=datetime.now(UTC),
        allow_memory_extraction=True,
    )
    try:
        event = await repository.record_event(source)
        _, job_id = await repository.prohibit_events(scope, [event.event_id])
        worked = await process_one_extraction(
            repository, Consolidator(HeuristicExtractor(), MemoryPolicy())
        )
        status = await repository.event_processing_status(scope, event.event_id)
        memories = await repository.list_memories(MemoryListQuery(scope=scope))
        while (await repository.deletion_status(scope, job_id or "")).status == "pending":
            assert await repository.process_one_deletion() is True
    finally:
        await repository.close()

    assert worked is False
    assert status.extraction_status == "failed"
    assert status.error_code == "prohibited_from_memory"
    assert memories == []


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_same_target_deletion_returns_one_job() -> None:
    url = _test_database_url()
    _upgrade_test_database(url)
    repository = PostgresRepository.from_url(url)
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    source = InteractionEvent(
        **scope.model_dump(),
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="并发删除目标",
        occurred_at=datetime.now(UTC),
    )
    try:
        event = await repository.record_event(source)
        memory_id = await repository.remember(
            scope, "并发删除目标", event.event_id, MemoryPolicy.version
        )
        first, second = await asyncio.gather(
            repository.isolate_memories(scope, [memory_id]),
            repository.isolate_memories(scope, [memory_id]),
        )
        while (await repository.deletion_status(scope, first[1] or "")).status == "pending":
            assert await repository.process_one_deletion() is True
    finally:
        await repository.close()

    assert first == second


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_deletion_stops_after_five_failures(monkeypatch) -> None:
    url = _test_database_url()
    _upgrade_test_database(url)
    repository = PostgresRepository.from_url(url)
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    source = InteractionEvent(
        **scope.model_dump(),
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="失败删除目标",
        occurred_at=datetime.now(UTC),
    )
    try:
        event = await repository.record_event(source)
        memory_id = await repository.remember(
            scope, "失败删除目标", event.event_id, MemoryPolicy.version
        )
        _, job_id = await repository.isolate_memories(scope, [memory_id])

        async def fail_deletion(_):
            raise RuntimeError("permanent failure")

        monkeypatch.setattr(repository, "_apply_deletion", fail_deletion)
        for _ in range(5):
            with pytest.raises(RuntimeError):
                await repository.process_one_deletion()
            async with repository.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE deletion_job SET available_at=now() WHERE id=%s",
                    (job_id,),
                )
        status = await repository.deletion_status(scope, job_id or "")
        no_more_work = await repository.process_one_deletion()
    finally:
        await repository.close()

    assert status.status == "failed"
    assert status.attempts == 5
    assert status.error_code == "RuntimeError"
    assert no_more_work is False


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_shadow_candidate_promotes_when_active_mode_is_enabled() -> None:
    url = _test_database_url()
    _upgrade_test_database(url)
    repository = PostgresRepository.from_url(url)
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    sources = [
        InteractionEvent(
            **scope.model_dump(),
            thread_id="thread-a",
            idempotency_key=f"event-{uuid4()}",
            source=EventSource.USER,
            content="请记住：我喜欢可验证的系统",
            occurred_at=datetime.now(UTC),
        )
        for _ in range(2)
    ]
    extractor = HeuristicExtractor()
    try:
        events = [await repository.record_event(source) for source in sources]
        await repository.store_candidates(
            events[0].event_id,
            sources[0],
            extractor.extract(sources[0]),
            MemoryPolicy.version,
            "quarantined",
        )
        before = await repository.list_memories(MemoryListQuery(scope=scope))
        await repository.store_candidates(
            events[1].event_id,
            sources[1],
            extractor.extract(sources[1]),
            MemoryPolicy.version,
            "active",
        )
        after = await repository.list_memories(MemoryListQuery(scope=scope))
    finally:
        await repository.close()

    assert before == []
    assert len(after) == 1
    assert set(after[0].source_event_ids) == {event.event_id for event in events}


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_history_recall_includes_superseded_version() -> None:
    url = _test_database_url()
    _upgrade_test_database(url)
    repository = PostgresRepository.from_url(url)
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    sources = [
        InteractionEvent(
            **scope.model_dump(),
            thread_id="thread-a",
            idempotency_key=f"event-{uuid4()}",
            source=EventSource.USER,
            content=content,
            occurred_at=datetime.now(UTC),
        )
        for content in ("以前喜欢长回答", "现在喜欢简洁回答")
    ]
    try:
        events = [await repository.record_event(source) for source in sources]
        original_id = await repository.remember(
            scope, "我喜欢长回答", events[0].event_id, MemoryPolicy.version
        )
        await repository.correct(
            scope,
            original_id,
            "我喜欢简洁回答",
            events[1].event_id,
            MemoryPolicy.version,
        )
        current = await repository.search(
            scope, "回答偏好", "conversation", datetime.now(UTC)
        )
        history = await repository.search(
            scope, "回答偏好", "history", datetime.now(UTC)
        )
    finally:
        await repository.close()

    assert [item.statement for item in current] == ["我喜欢简洁回答"]
    assert {item.statement for item in history} == {
        "我喜欢长回答",
        "我喜欢简洁回答",
    }

