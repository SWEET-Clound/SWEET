import asyncio
import base64
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient, Client, MockTransport, Response
from psycopg.errors import UndefinedColumn
from sqlalchemy.ext.asyncio import create_async_engine

from memory_system.api import JwtAuthenticator, create_app
from memory_system.consolidation import Consolidator, HeuristicExtractor
from memory_system.dev import check_worker_database
from memory_system.embedding import EmbeddingClient
from memory_system.event_cipher import EventCipher
from memory_system.llm import ModelResponseTooLarge
from memory_system.models import (
    CorrectCommand,
    CorrectProcedureCommand,
    DoNotRememberCommand,
    EventSource,
    ForgetCommand,
    InteractionEvent,
    MemoryCandidate,
    MemoryLayer,
    MemoryListQuery,
    MemoryScope,
    RecallRequest,
    RememberCommand,
    RememberProcedureCommand,
)
from memory_system.policy import MemoryPolicy
from memory_system.postgres import (
    DeletionCanaryFailed,
    EventPayloadUnreadable,
    IdempotencyConflictError,
    NotFoundError,
    PostgresRepository,
    RateLimitExceeded,
)
from memory_system.retrieval import render_data_only
from memory_system.system import MemorySystem
from memory_system.worker import backfill_embeddings, process_one_extraction


def _test_role_url(variable: str) -> str:
    url = os.getenv(variable, "")
    if not url:
        pytest.skip(f"{variable} is not configured")
    target = urlparse(url)
    if not target.hostname or target.query or not target.path.endswith("_test"):
        pytest.fail(f"{variable} must explicitly address a disposable *_test database without query parameters")
    if variable != "MEMORY_TEST_DATABASE_URL":
        owner = urlparse(_test_database_url())
        if (target.hostname, target.port, target.path) != (owner.hostname, owner.port, owner.path):
            pytest.fail(f"{variable} must point to the same disposable test database as the owner")
    return url


def _test_database_url() -> str:
    return _test_role_url("MEMORY_TEST_DATABASE_URL")


def _test_app_database_url() -> str:
    return _test_role_url("MEMORY_TEST_APP_DATABASE_URL")


def _upgrade_test_database(url: str, *, replay_scrub: bool = False) -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(root / "alembic.ini")
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    if replay_scrub:
        command.downgrade(config, "20261105_0009")
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
                "TRUNCATE memory_write_rate_limit, command_dedup, deletion_job, "
                "memory_audit, memory_outbox, "
                "memory_recall_diagnostic, memory_candidate_decision, memory_relation, "
                "memory_evidence, memory_item, memory_event RESTART IDENTITY CASCADE"
            )
    finally:
        await repository.close()
    yield


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_explicit_opt_out_blocks_evidence_but_allows_erasure(legacy: bool) -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a")
    try:
        receipt = await repository.record_event(
            InteractionEvent(
                **scope.model_dump(),
                thread_id="thread-a",
                idempotency_key="opt-out",
                source=EventSource.USER,
                content="请不要记住：我喜欢简洁回答",
                occurred_at=datetime.now(UTC),
                allow_memory_extraction=True,
            )
        )
        assert receipt.extraction_status == "disabled"
        if legacy:
            owner = PostgresRepository.from_url(_test_database_url())
            try:
                async with owner.engine.begin() as connection:
                    await connection.exec_driver_sql(
                        "UPDATE memory_event SET prohibited_from_memory=false WHERE id=%s",
                        (receipt.event_id,),
                    )
            finally:
                await owner.close()
        with pytest.raises(NotFoundError):
            await repository.remember(
                scope, "我喜欢简洁回答", receipt.event_id, MemoryPolicy.version
            )
        _, job_id = await repository.prohibit_events(scope, [receipt.event_id])
        assert job_id
        worker = PostgresRepository.from_url(_test_database_url())
        try:
            assert await worker.process_one_deletion() is True
            async with worker.engine.connect() as connection:
                result = await connection.exec_driver_sql(
                    "SELECT content, redacted_at IS NOT NULL FROM memory_event WHERE id=%s",
                    (receipt.event_id,),
                )
                assert tuple(result.one()) == ("[deleted-by-user]", True)
        finally:
            await worker.close()
        assert (await repository.deletion_status(scope, job_id)).status == "completed"
    finally:
        await repository.close()


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("schema_state", ["current", "hidden", "before_rotation_retry"])
async def test_api_database_readiness_checks_visible_schema(schema_state: str) -> None:
    options = {"options": "-c search_path=pg_catalog"} if schema_state == "hidden" else {}
    repository = PostgresRepository(
        create_async_engine(_test_app_database_url(), connect_args=options)
    )

    @asynccontextmanager
    async def lifespan(_app):
        yield

    app = create_app(
        MemorySystem(repository, MemoryPolicy(), 2048),
        JwtAuthenticator("issuer", "memory-api", "https://issuer.test/jwks"),
        lifespan,
    )
    try:
        if schema_state == "before_rotation_retry":
            root = Path(__file__).resolve().parents[1]
            config = Config(root / "alembic.ini")
            config.set_main_option("script_location", str(root / "migrations"))
            config.set_main_option("sqlalchemy.url", _test_database_url())
            worker_url = _test_role_url("MEMORY_TEST_WORKER_DATABASE_URL")
            command.downgrade(config, "20261106_0017")
        async with repository.engine.connect() as connection:
            assert (await connection.exec_driver_sql("SELECT 1")).scalar_one() == 1
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health/ready")
            if schema_state == "before_rotation_retry":
                with pytest.raises(UndefinedColumn, match="ciphertext_retry_at"):
                    check_worker_database(worker_url)
                command.upgrade(config, "head")
                check_worker_database(worker_url)
                assert (await client.get("/health/ready")).status_code == 200
    finally:
        await repository.close()
        if schema_state == "before_rotation_retry":
            _upgrade_test_database(_test_database_url())
    assert response.status_code == (200 if schema_state == "current" else 503)
    if schema_state != "current":
        assert response.json() == {"code": "memory_unavailable"}


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
async def test_write_limits_are_atomic_idempotent_and_scope_isolated() -> None:
    repository = PostgresRepository.from_url(
        _test_app_database_url(),
        event_write_limit_per_minute=1,
        command_write_limit_per_minute=1,
    )
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    source = InteractionEvent(
        **scope.model_dump(),
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="Please remember that I prefer concise answers",
        occurred_at=datetime.now(UTC),
    )
    system = MemorySystem(repository, MemoryPolicy(), 2048)
    command_key = f"command-{uuid4()}"
    command = RememberCommand(
        action="remember",
        scope=scope,
        statement="I prefer concise answers",
        source_event_id="pending",
    )
    try:
        event = await repository.record_event(source)
        replayed_event = await repository.record_event(source)
        with pytest.raises(RateLimitExceeded) as event_limit:
            await repository.record_event(
                source.model_copy(
                    update={
                        "idempotency_key": f"event-{uuid4()}",
                        "content": "A second event in the same minute",
                    }
                )
            )

        command = command.model_copy(update={"source_event_id": event.event_id})
        remembered = await system.execute_command_idempotent(
            command, command_key, "remember-payload"
        )
        replayed_command = await system.execute_command_idempotent(
            command, command_key, "remember-payload"
        )
        with pytest.raises(RateLimitExceeded) as command_limit:
            await system.execute_command_idempotent(
                command.model_copy(update={"statement": "I prefer verified answers"}),
                f"command-{uuid4()}",
                "second-command-payload",
            )

        other_scope_event = await repository.record_event(
            source.model_copy(
                update={
                    "user_id": "user-b",
                    "idempotency_key": f"event-{uuid4()}",
                    "content": "An isolated user's event",
                }
            )
        )
        active = await repository.list_memories(MemoryListQuery(scope=scope))
        deletion = await system.execute_command_idempotent(
            ForgetCommand(
                action="forget", scope=scope, memory_ids=remembered.affected_memory_ids
            ),
            f"command-{uuid4()}",
            "privacy-command",
        )
        prohibition = await system.execute_command_idempotent(
            DoNotRememberCommand(
                action="do_not_remember", scope=scope, event_ids=[event.event_id]
            ),
            f"command-{uuid4()}",
            "privacy-prohibition",
        )
    finally:
        await repository.close()

    owner = PostgresRepository.from_url(_test_database_url())
    try:
        async with owner.engine.connect() as connection:
            buckets = await connection.exec_driver_sql(
                "SELECT user_id, operation, request_count "
                "FROM memory_write_rate_limit WHERE tenant_id=%s ORDER BY user_id, operation",
                (scope.tenant_id,),
            )
            bucket_rows = list(buckets)
            persisted = await connection.exec_driver_sql(
                "SELECT "
                "(SELECT count(*) FROM memory_event WHERE tenant_id=%s AND user_id='user-a'), "
                "(SELECT count(*) FROM command_dedup WHERE tenant_id=%s AND user_id='user-a'), "
                "(SELECT prohibited_from_memory FROM memory_event WHERE id=%s)",
                (scope.tenant_id, scope.tenant_id, event.event_id),
            )
            persisted_counts = tuple(persisted.one())
            security = await connection.exec_driver_sql(
                "SELECT c.relrowsecurity, "
                "has_table_privilege('memory_app','memory_write_rate_limit',"
                "'SELECT,INSERT,UPDATE'), "
                "has_table_privilege('memory_app','memory_write_rate_limit','DELETE'), "
                "has_table_privilege('memory_worker','memory_write_rate_limit','SELECT,DELETE') "
                "FROM pg_class c WHERE c.relname='memory_write_rate_limit'"
            )
            security_contract = tuple(security.one())
    finally:
        await owner.close()

    assert replayed_event.event_id == event.event_id
    assert replayed_event.duplicate is True
    assert event_limit.value.operation == "event"
    assert replayed_command == remembered
    assert command_limit.value.operation == "command"
    assert other_scope_event.recorded is True
    assert [item.statement for item in active] == [command.statement]
    assert bucket_rows == [
        ("user-a", "command", 1),
        ("user-a", "event", 1),
        ("user-b", "event", 1),
    ]
    assert deletion.status == "deletion_pending"
    assert prohibition.status == "deletion_pending"
    assert persisted_counts == (1, 3, True)
    assert security_contract == (True, True, False, True)


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("use_worker_role, encrypted", [(False, False), (True, False), (True, True)])
async def test_event_to_automatic_memory_to_physical_deletion(
    use_worker_role: bool, encrypted: bool
) -> None:
    if use_worker_role:
        worker_url = _test_role_url("MEMORY_TEST_WORKER_DATABASE_URL")
        app_url = _test_app_database_url()
    else:
        worker_url = app_url = _test_database_url()
    key = base64.b64encode(os.urandom(32)).decode() if encrypted else None
    cipher = EventCipher(key) if key is not None else None
    repository = PostgresRepository.from_url(app_url, event_cipher=cipher)
    worker = PostgresRepository.from_url(worker_url, event_cipher=cipher)
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
        if cipher is None:
            receipt = await repository.record_event(source)
        else:
            legacy = PostgresRepository.from_url(app_url)
            try:
                source = source.model_copy(update={"metadata": {"legacy": "历史元数据"}})
                receipt = await legacy.record_event(source)
                async with worker.engine.begin() as connection:
                    await connection.exec_driver_sql(
                        "SELECT id FROM memory_event WHERE id=%s FOR UPDATE", (receipt.event_id,)
                    )
                    await asyncio.wait_for(worker.run_maintenance(30), timeout=3)
                    row = (await connection.exec_driver_sql(
                        "SELECT content, ciphertext FROM memory_event WHERE id=%s",
                        (receipt.event_id,),
                    )).one()
                    assert tuple(row) == (source.content, None)
                await worker.run_maintenance(30)
                async with worker.engine.connect() as connection:
                    row = (await connection.exec_driver_sql(
                        "SELECT content, metadata, ciphertext FROM memory_event WHERE id=%s",
                        (receipt.event_id,),
                    )).one()
                    assert row.content == "" and row.metadata == {}
                    assert cipher.decrypt(receipt.event_id, row.ciphertext) == {
                        "content": source.content, "metadata": source.metadata,
                    }
                    ciphertext = row.ciphertext
                await worker.run_maintenance(30)
                async with worker.engine.connect() as connection:
                    assert (await connection.exec_driver_sql(
                        "SELECT ciphertext FROM memory_event WHERE id=%s", (receipt.event_id,)
                    )).scalar_one() == ciphertext
            finally:
                await legacy.close()
        worked = await process_one_extraction(
            worker, Consolidator(HeuristicExtractor(), MemoryPolicy())
        )
        processing = await repository.event_processing_status(scope, receipt.event_id)
        memories = await repository.list_memories(MemoryListQuery(scope=scope))
        assert not await worker.store_embedding(
            memories[0].memory_id, "stale statement", "test-model", [1.0, 0.0]
        )
        requests = []

        def respond(request):
            requests.append(request)
            return Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

        transport = MockTransport(respond)
        with Client(base_url="http://embedding.test/v1/", transport=transport) as http:
            client = EmbeddingClient(http, "test-model", 2)
            async with worker.embedding_backfill_lock(client.model, client.dimensions) as acquired:
                assert acquired
                await backfill_embeddings(worker, client)
                assert not requests
            await backfill_embeddings(worker, client)
        assert len(requests) == 1
        assert await worker.pending_embeddings("test-model", 2) == []
        query_time = datetime.now(UTC)
        semantic = await repository.search(
            scope, "unrelated words", "task", query_time,
            embedding_model="test-model", query_embedding=[1.0, 0.0],
        )
        assert [item.memory_id for item in semantic] == [memories[0].memory_id]
        assert semantic[0].query_matched
        request = RecallRequest(
            **scope.model_dump(), thread_id=source.thread_id, query="unrelated words",
            purpose="task", token_budget=512, now=query_time,
        )
        with Client(base_url="http://embedding.test/v1/", transport=transport) as http:
            system = MemorySystem(repository, MemoryPolicy(), 2048, EmbeddingClient(http, "test-model", 2))
            context = await system.build_context(request)
            assert [item.memory_id for item in context.items] == [memories[0].memory_id]
        unavailable = MockTransport(lambda _: Response(503))
        with Client(base_url="http://embedding.test/v1/", transport=unavailable) as http:
            system = MemorySystem(repository, MemoryPolicy(), 2048, EmbeddingClient(http, "test-model", 2))
            context = await system.build_context(
                request.model_copy(update={"query": memories[0].statement})
            )
            assert [item.memory_id for item in context.items] == [memories[0].memory_id]
        for model, vector in (("other-model", [1.0, 0.0]), ("test-model", [1.0])):
            assert not await repository.search(
                scope, "unrelated words", "task", query_time,
                embedding_model=model, query_embedding=vector,
            )
        async with repository.engine.connect() as connection:
            await repository._set_scope(connection, scope)
            assert (await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_embedding"
            )).scalar_one() == 1
            await repository._set_scope(connection, scope.model_copy(update={"user_id": "other"}))
            assert (await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_embedding"
            )).scalar_one() == (0 if use_worker_role else 1)
        if use_worker_role:
            async with worker.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_item SET poisoning_flags=ARRAY['test_flag'] WHERE id=%s",
                    (memories[0].memory_id,),
                )
            async with repository.engine.connect() as connection:
                await repository._set_scope(connection, scope)
                assert (await connection.exec_driver_sql(
                    "SELECT count(*) FROM memory_embedding WHERE memory_id=%s",
                    (memories[0].memory_id,),
                )).scalar_one() == 0
            async with worker.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_item SET poisoning_flags='{}'::text[] WHERE id=%s",
                    (memories[0].memory_id,),
                )
        affected, deletion_job_id = await repository.isolate_memories(
            scope, [memories[0].memory_id]
        )
        assert not await worker.store_embedding(
            memories[0].memory_id, memories[0].statement, "test-model", [0.0, 1.0]
        )
        assert not await repository.search(
            scope, "unrelated words", "task", datetime.now(UTC),
            embedding_model="test-model", query_embedding=[1.0, 0.0],
        )
        assert not await repository.list_memories(
            MemoryListQuery(scope=scope, statuses=["active", "deleting"])
        )
        new_source = source.model_copy(update={"idempotency_key": "fresh-evidence"})
        new_receipt = await repository.record_event(new_source)
        consolidator = Consolidator(HeuristicExtractor(), MemoryPolicy())
        assert await process_one_extraction(worker, consolidator)
        deferred = await repository.event_processing_status(scope, new_receipt.event_id)
        assert deferred.extraction_status == "retry"
        assert deferred.error_code == "MemoryDeletionInProgress"
        deletion = await repository.deletion_status(scope, deletion_job_id or "")
        assert deletion.status == "pending"
        if use_worker_role:
            owner = PostgresRepository.from_url(_test_database_url())
            try:
                async with owner.engine.begin() as connection:
                    await connection.exec_driver_sql(
                        "CREATE FUNCTION test_retain_diagnostic() RETURNS trigger "
                        "LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$"
                    )
                    await connection.exec_driver_sql(
                        "CREATE TRIGGER test_retain_diagnostic BEFORE DELETE ON "
                        "memory_recall_diagnostic FOR EACH ROW EXECUTE FUNCTION "
                        "test_retain_diagnostic()"
                    )
                try:
                    with pytest.raises(DeletionCanaryFailed, match="deletion_canary_failed"):
                        await worker.process_one_deletion()
                    async with worker.engine.connect() as connection:
                        job = (await connection.exec_driver_sql(
                            "SELECT status, attempts, error_code FROM deletion_job WHERE id=%s",
                            (deletion_job_id,),
                        )).one()
                        assert tuple(job) == ("retry", 1, "DeletionCanaryFailed")
                        assert (await connection.exec_driver_sql(
                            "SELECT count(*) FROM memory_embedding WHERE memory_id=%s",
                            (memories[0].memory_id,),
                        )).scalar_one() == 1
                        event_row = (await connection.exec_driver_sql(
                            "SELECT content, ciphertext FROM memory_event WHERE id=%s",
                            (receipt.event_id,),
                        )).one()
                        if cipher is not None:
                            assert event_row.content == ""
                            assert cipher.decrypt(receipt.event_id, event_row.ciphertext) == {
                                "content": source.content, "metadata": source.metadata,
                            }
                        else:
                            assert tuple(event_row) == (source.content, None)
                finally:
                    async with owner.engine.begin() as connection:
                        await connection.exec_driver_sql(
                            "DROP TRIGGER test_retain_diagnostic ON memory_recall_diagnostic"
                        )
                        await connection.exec_driver_sql("DROP FUNCTION test_retain_diagnostic()")
                async with worker.engine.begin() as connection:
                    await connection.exec_driver_sql(
                        "UPDATE deletion_job SET available_at=now() WHERE id=%s",
                        (deletion_job_id,),
                    )
            finally:
                await owner.close()
        assert await worker.process_one_deletion() is True
        deletion = await repository.deletion_status(scope, deletion_job_id or "")
        remaining = await repository.list_memories(MemoryListQuery(scope=scope))
        async with worker.engine.connect() as connection:
            assert (await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_embedding WHERE memory_id=%s",
                (memories[0].memory_id,),
            )).scalar_one() == 0
            assert (await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_evidence WHERE memory_id=%s",
                (memories[0].memory_id,),
            )).scalar_one() == 0
            assert (await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_recall_diagnostic WHERE id=%s",
                (context.diagnostics_id,),
            )).scalar_one() == 0
        replay_after_deletion = await repository.record_event(source)
        async with worker.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE memory_outbox SET available_at=now() WHERE event_id=%s AND status='retry'",
                (new_receipt.event_id,),
            )
        assert await process_one_extraction(worker, consolidator)
        recovered = await repository.event_processing_status(scope, new_receipt.event_id)
        assert recovered.extraction_status == "completed"
        assert recovered.attempts == 2
        replacement, = await repository.list_memories(MemoryListQuery(scope=scope))
        assert replacement.memory_id != memories[0].memory_id
        assert replacement.source_event_ids == [new_receipt.event_id]
        if key is not None:
            await repository.execute_command_idempotent(
                DoNotRememberCommand(
                    action="do_not_remember", scope=scope, event_ids=[receipt.event_id]
                ),
                "erase-encrypted-event",
                "erase-encrypted-event",
                MemoryPolicy().version,
            )
            assert await worker.process_one_deletion()
            async with worker.engine.connect() as connection:
                assert tuple((await connection.exec_driver_sql(
                    "SELECT content, ciphertext FROM memory_event WHERE id=%s",
                    (receipt.event_id,),
                )).one()) == ("[deleted-by-user]", None)
            new_key = base64.b64encode(os.urandom(32)).decode()
            damaged = await repository.record_event(
                source.model_copy(update={"idempotency_key": "damaged-ciphertext"})
            )
            damaged_bytes = bytes(32)
            async with worker.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_event SET ciphertext=%s WHERE id=%s",
                    (damaged_bytes, damaged.event_id),
                )
            rotated = PostgresRepository.from_url(
                worker_url,
                event_cipher=EventCipher(new_key, key),
                rotate_event_ciphertext=True,
            )
            try:
                await rotated.run_maintenance(30)
                async with rotated.engine.connect() as connection:
                    row = (await connection.exec_driver_sql(
                        "SELECT ciphertext, ciphertext_key_id FROM memory_event WHERE id=%s",
                        (new_receipt.event_id,),
                    )).one()
                    new_cipher = EventCipher(new_key)
                    assert row.ciphertext_key_id == new_cipher.key_id
                    assert new_cipher.decrypt(new_receipt.event_id, row.ciphertext) == {
                        "content": new_source.content, "metadata": new_source.metadata,
                    }
                    failed = (await connection.exec_driver_sql(
                        "SELECT ciphertext, ciphertext_retry_at > now() AS deferred "
                        "FROM memory_event WHERE id=%s",
                        (damaged.event_id,),
                    )).one()
                    assert tuple(failed) == (damaged_bytes, True)
                following = await repository.record_event(
                    source.model_copy(update={"idempotency_key": "after-damaged-ciphertext"})
                )
                with pytest.raises(EventPayloadUnreadable):
                    await process_one_extraction(rotated, consolidator)
                failed_job = await repository.event_processing_status(scope, damaged.event_id)
                assert failed_job.extraction_status == "retry"
                assert failed_job.attempts == 1
                assert failed_job.error_code == "EventPayloadUnreadable"
                assert await process_one_extraction(rotated, consolidator)
                next_job = await repository.event_processing_status(scope, following.event_id)
                assert next_job.extraction_status == "completed"
            finally:
                await rotated.close()
    finally:
        await worker.close()
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
async def test_worker_does_not_retry_oversized_model_response() -> None:
    class OversizedExtractor(HeuristicExtractor):
        def extract(self, event):
            raise ModelResponseTooLarge()

    scope = MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a")
    source = InteractionEvent(
        **scope.model_dump(), thread_id="thread-a", idempotency_key="oversized-model",
        source=EventSource.USER, content="请记住：我喜欢简洁回答",
        occurred_at=datetime.now(UTC), allow_memory_extraction=True,
    )
    repository = PostgresRepository.from_url(_test_database_url())
    try:
        receipt = await repository.record_event(source)
        consolidator = Consolidator(OversizedExtractor(), MemoryPolicy())
        assert await process_one_extraction(repository, consolidator)
        status = await repository.event_processing_status(scope, receipt.event_id)
        assert status.extraction_status == "failed"
        assert status.attempts == 1
        assert status.error_code == "ModelResponseTooLarge"
        assert not await process_one_extraction(repository, consolidator)
        async with repository.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE memory_outbox SET status='processing', attempts=5, "
                "locked_at=now()-interval '6 minutes' WHERE event_id=%s",
                (receipt.event_id,),
            )
        assert await repository.claim_next_extraction() is None
        await repository.run_maintenance(raw_event_retention_days=30)
        exhausted = await repository.event_processing_status(scope, receipt.event_id)
        assert exhausted.extraction_status == "failed"
        assert exhausted.error_code == "lease_exhausted"
        assert exhausted.attempts == 5
    finally:
        await repository.close()


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
        with pytest.raises(NotFoundError):
            await repository.isolate_memories(scope_b, [memory_id])
    finally:
        await repository.close()

    assert [item.memory_id for item in visible_to_a] == [memory_id]
    assert visible_to_b == []


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_app_role_can_prohibit_an_event_and_remove_its_evidence() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    source = InteractionEvent(
        **scope.model_dump(),
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="请记住：随后禁止记忆",
        occurred_at=datetime.now(UTC),
    )
    try:
        event = await repository.record_event(source)
        memory_id = await repository.remember(
            scope, "随后禁止记忆", event.event_id, MemoryPolicy.version
        )
        affected, job_id = await repository.prohibit_events(scope, [event.event_id])
        audit = await repository.list_audit(scope, 10)
    finally:
        await repository.close()

    assert affected == [memory_id]
    assert job_id is not None
    assert {entry.action for entry in audit} >= {"remember", "do_not_remember"}


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_deleting_superseded_memory_keeps_its_replacement() -> None:
    url = _test_database_url()
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
        for content in ("旧偏好", "新偏好")
    ]
    try:
        events = [await repository.record_event(source) for source in sources]
        original_id = await repository.remember(
            scope, "旧偏好", events[0].event_id, MemoryPolicy.version
        )
        replacement_id = await repository.correct(
            scope,
            original_id,
            "新偏好",
            events[1].event_id,
            MemoryPolicy.version,
        )
        _, job_id = await repository.isolate_memories(scope, [original_id])
        assert job_id is not None
        assert await repository.process_one_deletion() is True
        memories = await repository.list_memories(MemoryListQuery(scope=scope))
        async with repository.engine.connect() as connection:
            supersedes_id = await connection.exec_driver_sql(
                "SELECT supersedes_id FROM memory_item WHERE id=%s",
                (replacement_id,),
            )
            replacement_parent = supersedes_id.scalar_one()
    finally:
        await repository.close()

    assert [item.memory_id for item in memories] == [replacement_id]
    assert replacement_parent is None


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
@pytest.mark.parametrize("crash_on_last_attempt", [False, True])
@pytest.mark.parametrize("by_event", [False, True])
async def test_deletion_stops_after_five_attempts(
    monkeypatch, crash_on_last_attempt: bool, by_event: bool
) -> None:
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
        submit = repository.prohibit_events if by_event else repository.isolate_memories
        targets = [event.event_id if by_event else memory_id]
        _, job_id = await submit(scope, targets)

        apply_deletion = repository._apply_deletion

        async def fail_deletion(row):
            if row["attempts"] == 2:
                stale = dict(row) | {"attempts": 1}
                assert not await apply_deletion(stale)
                await repository._fail_deletion(str(row["id"]), 1, "stale")
                status = await repository.deletion_status(scope, str(row["id"]))
                assert status.status == "processing"
            if crash_on_last_attempt and row["attempts"] == 5:
                raise asyncio.CancelledError
            raise RuntimeError("permanent failure")

        monkeypatch.setattr(repository, "_apply_deletion", fail_deletion)
        for _ in range(4 if crash_on_last_attempt else 5):
            with pytest.raises(RuntimeError):
                await repository.process_one_deletion()
            async with repository.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE deletion_job SET available_at=now() WHERE id=%s",
                    (job_id,),
                )
        if crash_on_last_attempt:
            with pytest.raises(asyncio.CancelledError):
                await repository.process_one_deletion()
            async with repository.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE deletion_job SET locked_at=now()-interval '6 minutes' WHERE id=%s",
                    (job_id,),
                )
            assert not await repository.process_one_deletion()
            await repository.run_maintenance(raw_event_retention_days=30)
        status = await repository.deletion_status(scope, job_id or "")
        no_more_work = await repository.process_one_deletion()
        monkeypatch.setattr(repository, "_apply_deletion", apply_deletion)
        if by_event:
            with pytest.raises(NotFoundError):
                await repository.isolate_memories(scope, [memory_id])
            assert not await repository.process_one_deletion()
        affected, retried_id = await submit(scope, targets)
        assert affected == [memory_id]
        assert retried_id and retried_id != job_id
        assert await repository.process_one_deletion()
        assert (await repository.deletion_status(scope, retried_id)).status == "completed"
        async with repository.engine.connect() as connection:
            erased = await connection.exec_driver_sql(
                "SELECT NOT EXISTS (SELECT 1 FROM memory_item WHERE id=%s), "
                "(SELECT content FROM memory_event WHERE id=%s)",
                (memory_id, event.event_id),
            )
            assert tuple(erased.one()) == (
                True, "[deleted-by-user]" if by_event else source.content
            )
    finally:
        await repository.close()

    assert status.status == "failed"
    assert status.attempts == 5
    assert status.error_code == ("lease_exhausted" if crash_on_last_attempt else "RuntimeError")
    assert no_more_work is False


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_only_one_replica_runs_maintenance_at_a_time() -> None:
    url = _test_database_url()
    repository = PostgresRepository.from_url(url)
    memory_id = str(uuid4())
    episodic_id = str(uuid4())
    try:
        async with repository.engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                INSERT INTO memory_item (
                    id, tenant_id, user_id, agent_id, statement, layer, kind,
                    status, confidence, policy_version, expires_at
                ) VALUES (%s, 'tenant-a', 'user-a', 'agent-a', 'expired', 'L1',
                          'test', 'active', 1, 'test', now() - interval '1 hour')
                """,
                (memory_id,),
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO memory_item (
                    id, tenant_id, user_id, agent_id, statement, layer, kind,
                    status, confidence, policy_version, expires_at
                ) VALUES (%s, 'tenant-a', 'user-a', 'agent-a', 'archived event', 'L2',
                          'test', 'active', 1, 'test', now() - interval '1 hour')
                """,
                (episodic_id,),
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO memory_write_rate_limit (
                    tenant_id, user_id, agent_id, operation,
                    window_started_at, request_count
                ) VALUES (
                    'tenant-a', 'user-a', 'agent-a', 'event',
                    date_trunc('minute', now() - interval '2 days'), 1
                )
                """
            )
        async with repository.engine.begin() as leader:
            await leader.exec_driver_sql(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('persistent-memory-maintenance', 0))"
            )
            await repository.run_maintenance(raw_event_retention_days=30)
            async with repository.engine.connect() as observer:
                skipped_status = await observer.exec_driver_sql(
                    "SELECT id, status FROM memory_item WHERE id IN (%s, %s)",
                    (memory_id, episodic_id),
                )
                assert {row[1] for row in skipped_status} == {"active"}
        await repository.run_maintenance(raw_event_retention_days=30)
        async with repository.engine.connect() as observer:
            completed_status = await observer.exec_driver_sql(
                "SELECT id, status FROM memory_item WHERE id IN (%s, %s)",
                (memory_id, episodic_id),
            )
            statuses = {str(row[0]): row[1] for row in completed_status}
            old_rate_limits = await observer.exec_driver_sql(
                "SELECT count(*) FROM memory_write_rate_limit "
                "WHERE window_started_at < now() - interval '1 day'"
            )
            old_rate_limit_count = old_rate_limits.scalar_one()
        normal = await repository.search(
            MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a"),
            "archived event",
            "conversation",
            datetime.now(UTC),
        )
        history = await repository.search(
            MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a"),
            "archived event",
            "history",
            datetime.now(UTC),
        )
    finally:
        await repository.close()

    assert statuses == {memory_id: "expired", episodic_id: "archived"}
    assert old_rate_limit_count == 0
    assert normal == []
    assert [item.memory_id for item in history] == [episodic_id]


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
    assert after[0].sensitivity == "low"
    assert after[0].poisoning_flags == []


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_quarantined_candidates_have_audited_review_flow() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    sources = [
        InteractionEvent(
            **scope.model_dump(),
            thread_id="thread-a",
            idempotency_key=f"event-{uuid4()}",
            source=EventSource.USER,
            content=f"请记住：候选 {number}",
            occurred_at=datetime.now(UTC),
        )
        for number in (1, 2)
    ]
    extractor = HeuristicExtractor()
    try:
        events = [await repository.record_event(source) for source in sources]
        memory_ids = [
            (
                await repository.store_candidates(
                    event.event_id,
                    source,
                    extractor.extract(source),
                    MemoryPolicy.version,
                    "quarantined",
                )
            )[0]
            for event, source in zip(events, sources, strict=True)
        ]
        approved = await repository.review_memory(scope, memory_ids[0], "approve")
        rejected = await repository.review_memory(scope, memory_ids[1], "reject")
        active = await repository.list_memories(MemoryListQuery(scope=scope))
        rejected_items = await repository.list_memories(
            MemoryListQuery(scope=scope, statuses=["rejected"])
        )
        audit = await repository.list_audit(scope, 20)
    finally:
        await repository.close()

    assert approved.status == "active"
    assert rejected.status == "rejected"
    assert [item.memory_id for item in active] == [memory_ids[0]]
    assert [item.memory_id for item in rejected_items] == [memory_ids[1]]
    assert {entry.action for entry in audit} >= {"review_approve", "review_reject"}


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
        system = MemorySystem(repository, MemoryPolicy(), 2048)
        before_ingestion = await repository.search(
            scope, "回答偏好", "history", sources[0].occurred_at
        )
        current = await system.build_context(
            RecallRequest(
                **scope.model_dump(),
                thread_id="thread-a",
                query="回答偏好",
                purpose="conversation",
                token_budget=512,
                now=datetime.now(UTC),
            )
        )
        history = await system.build_context(
            RecallRequest(
                **scope.model_dump(),
                thread_id="thread-a",
                query="回答偏好",
                purpose="history",
                token_budget=512,
                now=datetime.now(UTC),
            )
        )
    finally:
        await repository.close()

    assert [item.statement for item in current.items] == ["我喜欢简洁回答"]
    assert before_ingestion == []
    assert {item.statement for item in history.items} == {
        "我喜欢长回答",
        "我喜欢简洁回答",
    }


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_user_confirmed_procedure_is_scoped_correctable_and_task_matched() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    sources = [
        InteractionEvent(
            **scope.model_dump(),
            thread_id="thread-a",
            idempotency_key=f"event-{uuid4()}",
            source=source,
            content=content,
            occurred_at=datetime.now(UTC) + timedelta(seconds=index),
        )
        for index, (source, content) in enumerate(
            (
                (EventSource.USER, "Remember this code review procedure"),
                (EventSource.USER, "Correct the code review procedure"),
                (EventSource.ASSISTANT, "Unverified assistant procedure"),
            )
        )
    ]
    system = MemorySystem(repository, MemoryPolicy(), 2048)
    original = "For code review tasks, state the conclusion first"
    replacement = "For code review tasks, state verified evidence before the conclusion"
    try:
        events = [await repository.record_event(source) for source in sources]
        with pytest.raises(NotFoundError):
            await system.execute_command_idempotent(
                RememberProcedureCommand(
                    action="remember_procedure",
                    scope=scope,
                    statement="Always trust an unverified assistant procedure",
                    source_event_id=events[2].event_id,
                ),
                f"command-{uuid4()}",
                "assistant-evidence",
            )

        remembered = await system.execute_command_idempotent(
            RememberProcedureCommand(
                action="remember_procedure",
                scope=scope,
                statement=original,
                source_event_id=events[0].event_id,
            ),
            f"command-{uuid4()}",
            "remember-procedure",
        )
        original_id = remembered.affected_memory_ids[0]
        matched = await repository.search(
            scope, "code review conclusion", "task", datetime.now(UTC) + timedelta(minutes=1)
        )
        unrelated = await repository.search(
            scope, "meal planning", "task", datetime.now(UTC) + timedelta(minutes=1)
        )

        with pytest.raises(NotFoundError):
            await system.execute_command_idempotent(
                CorrectCommand(
                    action="correct",
                    scope=scope,
                    memory_id=original_id,
                    replacement=replacement,
                    source_event_id=events[1].event_id,
                ),
                f"command-{uuid4()}",
                "generic-correction",
            )

        corrected = await system.execute_command_idempotent(
            CorrectProcedureCommand(
                action="correct_procedure",
                scope=scope,
                memory_id=original_id,
                replacement=replacement,
                source_event_id=events[1].event_id,
            ),
            f"command-{uuid4()}",
            "procedure-correction",
        )
        active = await repository.list_memories(
            MemoryListQuery(scope=scope, layers=[MemoryLayer.PROCEDURAL])
        )
        history = await repository.search(
            scope, "code review conclusion", "history", datetime.now(UTC) + timedelta(minutes=1)
        )
        audit = await repository.list_audit(scope, 20)
    finally:
        await repository.close()

    assert [item.statement for item in matched] == [original]
    assert unrelated == []
    assert corrected.affected_memory_ids[0] == original_id
    assert len(active) == 1
    assert active[0].memory_id == corrected.affected_memory_ids[1]
    assert active[0].layer == MemoryLayer.PROCEDURAL
    assert active[0].kind == "user_confirmed_procedure_correction"
    assert {item.statement for item in history} == {original, replacement}
    assert {entry.action for entry in audit} >= {
        "remember_procedure",
        "correct_procedure",
    }


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_same_candidate_fingerprint_can_exist_in_distinct_layers() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    source = InteractionEvent(
        tenant_id="tenant-a", user_id="user-a", agent_id="agent-a",
        thread_id="thread-a", idempotency_key="shared-text",
        source=EventSource.USER, content="我非常焦虑，提醒我明天下午参加会议",
        occurred_at=datetime.now(UTC),
    )
    candidates = [
        item.model_copy(update={"kind": "shared_kind"})
        for item in HeuristicExtractor().extract(source)
    ]
    try:
        receipt = await repository.record_event(source)
        first = await repository.store_candidates(
            receipt.event_id, source, candidates, MemoryPolicy.version
        )
        replay = await repository.store_candidates(
            receipt.event_id, source, candidates, MemoryPolicy.version
        )
        items = await repository.list_memories(
            MemoryListQuery(scope=MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a"))
        )
    finally:
        await repository.close()
    assert len(set(first)) == 2
    assert replay == first
    assert {item.layer for item in items} == {MemoryLayer.WORKING, MemoryLayer.EPISODIC}


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_rrf_recall_keeps_stable_order_at_lexical_candidate_cutoff() -> None:
    owner = PostgresRepository.from_url(_test_database_url())
    scope = MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a")
    try:
        async with owner.engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                INSERT INTO memory_item (
                    id, tenant_id, user_id, agent_id, statement, layer, kind,
                    status, confidence, policy_version
                )
                SELECT ('00000000-0000-0000-0000-' || lpad(n::text, 12, '0'))::uuid,
                       'tenant-a', 'user-a', 'agent-a', '项目交接清单',
                       'L3', 'test', 'active', 1, 'test'
                FROM generate_series(41, 1, -1) AS n
                """
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO memory_item (
                    id, tenant_id, user_id, agent_id, statement, layer, kind,
                    status, confidence, policy_version, future_utility
                ) VALUES (%s, 'tenant-a', 'user-a', 'agent-a', '周末烹饪课程',
                          'L2', 'test', 'active', 1, 'test', 'high')
                """,
                (str(uuid4()),),
            )
    finally:
        await owner.close()
    reader = PostgresRepository.from_url(_test_app_database_url())
    try:
        results = await reader.search(scope, "项目交接清单", "task", datetime.now(UTC), limit=40)
    finally:
        await reader.close()

    assert [item.memory_id for item in results] == [
        f"00000000-0000-0000-0000-{n:012d}" for n in range(1, 41)
    ]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_exact_subject_survives_trigram_ties_at_candidate_cutoff() -> None:
    owner = PostgresRepository.from_url(_test_database_url())
    scope = MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a")
    try:
        async with owner.engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                INSERT INTO memory_item (
                    id, tenant_id, user_id, agent_id, statement, layer, kind,
                    status, confidence, policy_version, subject_key
                )
                SELECT ('00000000-0000-0000-0000-' || lpad(n::text, 12, '0'))::uuid,
                       'tenant-a', 'user-a', 'agent-a', '无关正文', 'L3',
                       'test', 'active', 1, 'test',
                       'calendar:project-meeting' || repeat('!', 21 - n)
                FROM generate_series(1, 21) AS n
                """
            )
    finally:
        await owner.close()
    reader = PostgresRepository.from_url(_test_app_database_url())
    try:
        result = await reader.search(
            scope, "calendar:project-meeting", "task", datetime.now(UTC), limit=1
        )
    finally:
        await reader.close()
    assert [item.memory_id for item in result] == ["00000000-0000-0000-0000-000000000021"]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_rrf_recall_includes_exact_subject_and_recent_episode() -> None:
    url = _test_database_url()
    repository = PostgresRepository.from_url(url)
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    source = InteractionEvent(
        **scope.model_dump(),
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="下周一上午十点有项目会议",
        occurred_at=datetime.now(UTC),
    )
    candidate = MemoryCandidate(
        statement="下周一上午十点有项目会议",
        proposed_layer=MemoryLayer.EPISODIC,
        kind="scheduled_event",
        subject_key="calendar:project-meeting",
        valid_from=source.occurred_at,
        valid_to=None,
        expires_at=source.occurred_at + timedelta(days=7),
        source_event_ids=[],
        confidence=0.9,
        future_utility="medium",
        durability="uncertain",
        affect=None,
        sensitivity="low",
        poisoning_flags=[],
    )
    try:
        event = await repository.record_event(source)
        await repository.store_candidates(
            event.event_id,
            source,
            [
                candidate,
                candidate.model_copy(
                    update={
                        "statement": "周末有烹饪课程",
                        "subject_key": "calendar:cooking-class",
                        "valid_from": source.occurred_at + timedelta(seconds=1),
                    }
                ),
                candidate.model_copy(
                    update={
                        "statement": "项目会务会议",
                        "subject_key": None,
                        "proposed_layer": MemoryLayer.SEMANTIC,
                        "future_utility": "high",
                    }
                ),
            ],
            MemoryPolicy.version,
        )
        now = source.occurred_at + timedelta(minutes=1)
        exact = await repository.search(
            scope, "calendar:project-meeting", "task", now, limit=1
        )
        lexical = await repository.search(scope, "项目会议", "task", now, limit=1)
        recent = await repository.search(
            scope, "completely unrelated query", "task", now, limit=1
        )
    finally:
        await repository.close()

    assert [item.statement for item in exact] == [candidate.statement]
    assert [item.statement for item in lexical] == [candidate.statement]
    assert [item.statement for item in recent] == ["周末有烹饪课程"]
    assert exact[0].query_matched and lexical[0].query_matched
    assert not recent[0].query_matched


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_core_profile_is_rebuildable_scoped_and_excludes_unsafe_l3() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    now = datetime.now(UTC)

    def source(content: str, offset: int) -> InteractionEvent:
        return InteractionEvent(
            **scope.model_dump(),
            thread_id="thread-a",
            idempotency_key=f"event-{uuid4()}",
            source=EventSource.USER,
            content=content,
            occurred_at=now + timedelta(seconds=offset),
        )

    def candidate(
        event: InteractionEvent,
        statement: str,
        *,
        layer: MemoryLayer = MemoryLayer.SEMANTIC,
        subject_key: str | None = None,
        sensitivity: str = "low",
        utility: str = "high",
        durability: str = "stable",
    ) -> MemoryCandidate:
        return MemoryCandidate(
            statement=statement,
            proposed_layer=layer,
            kind="preference" if layer is MemoryLayer.SEMANTIC else "scheduled_event",
            subject_key=subject_key,
            valid_from=event.occurred_at,
            valid_to=None,
            expires_at=(event.occurred_at + timedelta(days=7))
            if layer is MemoryLayer.EPISODIC
            else None,
            source_event_ids=[],
            confidence=0.95,
            future_utility=utility,
            durability=durability,
            affect=None,
            sensitivity=sensitivity,
            poisoning_flags=[],
        )

    core_source = source("请记住我偏好简洁回答", 0)
    conflict_sources = [
        source("请记住我喜欢深色主题", 1),
        source("请记住我喜欢浅色主题", 2),
    ]
    medium_source = source("请记住一个中敏感偏好", 3)
    episode_source = source("下周一上午十点有项目会议", 4)
    sources = [core_source, *conflict_sources, medium_source, episode_source]
    try:
        receipts = [await repository.record_event(event) for event in sources]
        core_id = (
            await repository.store_candidates(
                receipts[0].event_id,
                core_source,
                [
                    candidate(
                        core_source,
                        "用户偏好简洁回答",
                        subject_key="preferences:response-style",
                    )
                ],
                MemoryPolicy.version,
            )
        )[0]
        for index, event in enumerate(conflict_sources, start=1):
            await repository.store_candidates(
                receipts[index].event_id,
                event,
                [
                    candidate(
                        event,
                        event.content,
                        subject_key="preferences:theme",
                    )
                ],
                MemoryPolicy.version,
            )
        await repository.store_candidates(
            receipts[3].event_id,
            medium_source,
            [candidate(medium_source, medium_source.content, sensitivity="medium")],
            MemoryPolicy.version,
        )
        await repository.store_candidates(
            receipts[4].event_id,
            episode_source,
            [
                candidate(
                    episode_source,
                    episode_source.content,
                    layer=MemoryLayer.EPISODIC,
                    durability="uncertain",
                )
            ],
            MemoryPolicy.version,
        )

        initial = await repository.profile(scope, now + timedelta(seconds=5))
        hidden = await repository.profile(
            scope.model_copy(update={"user_id": "other-user"}),
            now + timedelta(seconds=5),
        )

        correction_source = source("更正：我偏好详细回答", 6)
        correction_event = await repository.record_event(correction_source)
        corrected_id = await repository.correct(
            scope,
            core_id,
            "用户偏好详细回答",
            correction_event.event_id,
            MemoryPolicy.version,
        )
        corrected = await repository.profile(scope, now + timedelta(seconds=7))
        context = await MemorySystem(repository, MemoryPolicy(), 2048).build_context(
            RecallRequest(
                **scope.model_dump(),
                thread_id="thread-a",
                query="完全无关的查询",
                purpose="conversation",
                token_budget=512,
                now=now + timedelta(seconds=7),
            )
        )
    finally:
        await repository.close()

    assert [item.memory_id for item in initial] == [core_id]
    assert hidden == []
    assert [item.memory_id for item in corrected] == [corrected_id]
    assert corrected[0].statement == "用户偏好详细回答"
    assert context.items[0].memory_id == corrected_id


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_same_subject_candidates_are_kept_as_an_unresolved_conflict() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    sources = [
        InteractionEvent(
            **scope.model_dump(),
            thread_id="thread-a",
            idempotency_key=f"event-{uuid4()}",
            source=EventSource.USER,
            content=statement,
            occurred_at=datetime.now(UTC) + timedelta(hours=index),
        )
        for index, statement in enumerate(("我喜欢深色主题", "我喜欢浅色主题"))
    ]
    candidates = [
        MemoryCandidate(
            statement=source.content,
            proposed_layer=MemoryLayer.SEMANTIC,
            kind="preference",
            subject_key="preferences:theme",
            valid_from=source.occurred_at,
            valid_to=None,
            source_event_ids=[],
            confidence=0.9,
            future_utility="high",
            durability="stable",
            affect=None,
            sensitivity="low",
            poisoning_flags=[],
        )
        for source in sources
    ]
    try:
        events = [await repository.record_event(source) for source in sources]
        stored = await asyncio.gather(
            *(
                repository.store_candidates(
                    event.event_id, source, [candidate], MemoryPolicy.version
                )
                for event, source, candidate in zip(
                    events, sources, candidates, strict=True
                )
            )
        )
        memory_ids = [ids[0] for ids in stored]
        listed = await repository.list_memories(MemoryListQuery(scope=scope))
        before_effective_at = datetime.now(UTC)
        before_effective = await repository.search(
            scope, "preferences:theme", "conversation", before_effective_at
        )
        profile_before_effective = await repository.profile(scope, before_effective_at)
        profile_after_effective = await repository.profile(
            scope, sources[1].occurred_at + timedelta(seconds=1)
        )
        recalled = await repository.search(
            scope,
            "preferences:theme",
            "conversation",
            max(source.occurred_at for source in sources) + timedelta(seconds=1),
        )
        audit = await repository.list_audit(scope, 20)
        owner = PostgresRepository.from_url(_test_database_url())
        try:
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_relation SET created_at=now() + interval '1 day' "
                    "WHERE tenant_id=%s AND relation='contradicts'",
                    (scope.tenant_id,),
                )
        finally:
            await owner.close()
        before_conflict = await repository.search(
            scope, "preferences:theme", "history", datetime.now(UTC)
        )
        correction_source = sources[0].model_copy(
            update={
                "idempotency_key": f"event-{uuid4()}",
                "content": "刚才说错了，我喜欢浅色主题",
                "occurred_at": max(source.occurred_at for source in sources)
                + timedelta(seconds=2),
            }
        )
        correction_event = await repository.record_event(correction_source)
        resolved_id = await repository.correct(
            scope,
            memory_ids[0],
            sources[1].content,
            correction_event.event_id,
            MemoryPolicy.version,
        )
        resolved = await repository.list_memories(MemoryListQuery(scope=scope))
        profile = await repository.profile(
            scope,
            max(source.occurred_at for source in sources) + timedelta(seconds=3),
        )
    finally:
        await repository.close()

    assert {item.statement for item in listed} == {source.content for source in sources}
    by_id = {item.memory_id: item for item in listed}
    assert by_id[memory_ids[0]].conflict_memory_ids == [memory_ids[1]]
    assert by_id[memory_ids[1]].conflict_memory_ids == [memory_ids[0]]
    assert {item.memory_id for item in recalled} == set(memory_ids)
    assert all(item.conflict_memory_ids for item in recalled)
    assert [item.memory_id for item in before_effective] == [memory_ids[0]]
    assert not before_effective[0].conflict_memory_ids
    assert [item.memory_id for item in profile_before_effective] == [memory_ids[0]]
    assert profile_after_effective == []
    assert {item.memory_id for item in before_conflict} == set(memory_ids)
    assert all(not item.conflict_memory_ids for item in before_conflict)
    rendered = render_data_only(recalled)
    assert rendered.count('"requires_confirmation":true') == 2
    assert any(entry.action == "conflict_detected" for entry in audit)
    assert resolved_id == memory_ids[1]
    assert [item.memory_id for item in resolved] == [memory_ids[1]]
    assert resolved[0].conflict_memory_ids == []
    assert resolved[0].kind == "explicit_correction"
    assert resolved[0].confidence == 1.0
    assert [item.memory_id for item in profile] == [memory_ids[1]]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_approving_shadow_candidate_detects_existing_conflict() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    sources = [
        InteractionEvent(
            **scope.model_dump(),
            thread_id="thread-a",
            idempotency_key=f"event-{uuid4()}",
            source=EventSource.USER,
            content=statement,
            occurred_at=datetime.now(UTC),
        )
        for statement in ("我偏好深色主题", "我偏好浅色主题")
    ]
    candidates = [
        MemoryCandidate(
            statement=source.content,
            proposed_layer=MemoryLayer.SEMANTIC,
            kind="preference",
            subject_key="preferences:theme",
            valid_from=source.occurred_at,
            valid_to=None,
            source_event_ids=[],
            confidence=0.9,
            future_utility="high",
            durability="stable",
            affect=None,
            sensitivity="low",
            poisoning_flags=[],
        )
        for source in sources
    ]
    try:
        events = [await repository.record_event(source) for source in sources]
        active_id = (
            await repository.store_candidates(
                events[0].event_id, sources[0], [candidates[0]], MemoryPolicy.version
            )
        )[0]
        shadow_id = (
            await repository.store_candidates(
                events[1].event_id,
                sources[1],
                [candidates[1]],
                MemoryPolicy.version,
                "quarantined",
            )
        )[0]
        before = await repository.list_memories(MemoryListQuery(scope=scope))
        await repository.review_memory(scope, shadow_id, "approve")
        after = await repository.list_memories(MemoryListQuery(scope=scope))
    finally:
        await repository.close()

    assert [item.memory_id for item in before] == [active_id]
    by_id = {item.memory_id: item for item in after}
    assert by_id[active_id].conflict_memory_ids == [shadow_id]
    assert by_id[shadow_id].conflict_memory_ids == [active_id]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_candidate_decisions_are_auditable_without_candidate_text() -> None:
    class FixedExtractor:
        def __init__(self, candidates):
            self.candidates = candidates

        def extract(self, event):
            return self.candidates

    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    event = InteractionEvent(
        **scope.model_dump(),
        thread_id="thread-a",
        idempotency_key=f"event-{uuid4()}",
        source=EventSource.USER,
        content="请记住：我偏好简洁回答",
        occurred_at=datetime.now(UTC),
        allow_memory_extraction=True,
    )
    candidates = [
        MemoryCandidate(
            statement="我偏好简洁回答",
            proposed_layer=MemoryLayer.SEMANTIC,
            kind="preference",
            subject_key="preferences:response-style",
            valid_from=event.occurred_at,
            valid_to=None,
            source_event_ids=[],
            confidence=0.95,
            future_utility="high",
            durability="stable",
            affect=None,
            sensitivity="low",
            poisoning_flags=[],
        ),
        MemoryCandidate(
            statement="diagnosis that must not be copied into the audit ledger",
            proposed_layer=MemoryLayer.SEMANTIC,
            kind="health",
            subject_key="health:diagnosis",
            valid_from=event.occurred_at,
            valid_to=None,
            source_event_ids=[],
            confidence=0.95,
            future_utility="high",
            durability="stable",
            affect=None,
            sensitivity="high",
            poisoning_flags=[],
        ),
    ]
    try:
        receipt = await repository.record_event(event)
        worker = PostgresRepository.from_url(_test_database_url())
        try:
            first = await worker.claim_next_extraction()
            assert first is not None and first.event_id == receipt.event_id
            async with worker.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_outbox SET locked_at=now()-interval '6 minutes' WHERE id=%s",
                    (first.job_id,),
                )
            current = await worker.claim_next_extraction()
            assert current is not None and current.attempts == first.attempts + 1
            consolidator = Consolidator(FixedExtractor(candidates), MemoryPolicy())
            assert await consolidator.process(worker, first) == []
            assert await repository.list_candidate_decisions(scope, 10) == []
            await worker.complete_extraction(first.job_id, first.attempts)
            await worker.fail_extraction(first.job_id, first.attempts, "stale", terminal=True)
            pending = await repository.event_processing_status(scope, receipt.event_id)
            assert pending.extraction_status == "processing"
            await consolidator.process(worker, current)
            await worker.complete_extraction(current.job_id, current.attempts)
        finally:
            await worker.close()
        decisions = await repository.list_candidate_decisions(scope, 10)
        hidden = await repository.list_candidate_decisions(
            scope.model_copy(update={"user_id": "other-user"}), 10
        )
    finally:
        await repository.close()

    owner = PostgresRepository.from_url(_test_database_url())
    try:
        async with owner.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE memory_candidate_decision SET kind='legacy-private-kind', "
                "subject_key='legacy-private-subject', poisoning_flags=ARRAY['legacy-private-flag'] "
                "WHERE event_id=%s AND outcome='rejected'",
                (receipt.event_id,),
            )
        _upgrade_test_database(_test_database_url(), replay_scrub=True)
        async with owner.engine.connect() as connection:
            scrubbed = await connection.exec_driver_sql(
                "SELECT kind, subject_key, poisoning_flags FROM memory_candidate_decision "
                "WHERE event_id=%s AND outcome='rejected'",
                (receipt.event_id,),
            )
            assert tuple(scrubbed.one()) == ("redacted", None, [])
            columns = await connection.exec_driver_sql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='memory_candidate_decision'"
            )
            column_names = {row[0] for row in columns}
    finally:
        await owner.close()

    assert {decision.outcome for decision in decisions} == {"rejected", "stored"}
    by_outcome = {decision.outcome: decision for decision in decisions}
    assert by_outcome["stored"].stored_memory_id is not None
    assert by_outcome["rejected"].stored_memory_id is None
    assert by_outcome["rejected"].kind == "redacted"
    assert by_outcome["rejected"].subject_key is None
    assert by_outcome["rejected"].poisoning_flags == []
    reader = PostgresRepository.from_url(_test_app_database_url())
    try:
        legacy = await reader.list_candidate_decisions(scope, 10)
        assert next(item for item in legacy if item.outcome == "rejected") == by_outcome["rejected"]
        diagnostic_id = str(uuid4())
        memory_id = by_outcome["stored"].stored_memory_id
        assert memory_id is not None
        await reader.record_recall_diagnostic(
            scope, diagnostic_id, "conversation", 128, 1, [memory_id], False
        )
        _, deletion_id = await reader.prohibit_events(scope, [receipt.event_id])
        assert deletion_id
        assert (await reader.deletion_status(scope, deletion_id)).status == "pending"
        assert await reader.list_candidate_decisions(scope, 10) == []
        worker = PostgresRepository.from_url(_test_database_url())
        try:
            assert await worker.process_one_deletion()
        finally:
            await worker.close()
        assert (await reader.deletion_status(scope, deletion_id)).status == "completed"
        assert await reader.list_candidate_decisions(scope, 10) == []
        with pytest.raises(NotFoundError):
            await reader.get_recall_diagnostic(scope, diagnostic_id)
        late_id = str(uuid4())
        await reader.record_recall_diagnostic(
            scope, late_id, "conversation", 128, 1, [memory_id], False
        )
        assert (await reader.get_recall_diagnostic(scope, late_id)).selected_memory_ids == []
        owner = PostgresRepository.from_url(_test_database_url())
        try:
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_recall_diagnostic SET selected_memory_ids=ARRAY[%s::uuid] "
                    "WHERE id=%s",
                    (memory_id, late_id),
                )
            assert (await reader.get_recall_diagnostic(scope, late_id)).selected_memory_ids == []
            _upgrade_test_database(_test_database_url(), replay_scrub=True)
            async with owner.engine.connect() as connection:
                scrubbed = await connection.exec_driver_sql(
                    "SELECT selected_memory_ids FROM memory_recall_diagnostic WHERE id=%s",
                    (late_id,),
                )
                assert scrubbed.scalar_one() == []
        finally:
            await owner.close()
        assert (await reader.get_recall_diagnostic(scope, late_id)).selected_memory_ids == []
    finally:
        await reader.close()
    assert by_outcome["rejected"].reason == "sensitive_content_requires_explicit_consent"
    assert hidden == []
    assert "statement" not in column_names


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_recall_diagnostics_are_scoped_and_do_not_store_query_text() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(
        tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a"
    )
    diagnostics_id = str(uuid4())
    selected_id = str(uuid4())
    try:
        await repository.record_recall_diagnostic(
            scope,
            diagnostics_id,
            "conversation",
            128,
            3,
            [selected_id],
            True,
        )
        visible = await repository.get_recall_diagnostic(scope, diagnostics_id)
        with pytest.raises(NotFoundError):
            await repository.get_recall_diagnostic(
                scope.model_copy(update={"user_id": "other-user"}), diagnostics_id
            )
    finally:
        await repository.close()

    owner = PostgresRepository.from_url(_test_database_url())
    try:
        async with owner.engine.connect() as connection:
            columns = await connection.exec_driver_sql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='memory_recall_diagnostic'"
            )
            column_names = {row[0] for row in columns}
    finally:
        await owner.close()

    assert visible.diagnostics_id == diagnostics_id
    assert visible.selected_memory_ids == []
    assert visible.candidate_count == 3
    assert visible.truncated is True
    assert not {"query", "query_hash"} & column_names

