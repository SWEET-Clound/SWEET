import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient, Client, MockTransport, Response
from psycopg.errors import UndefinedColumn, UndefinedTable
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from memory_system.api import JwtAuthenticator, create_app
from memory_system.consolidation import Consolidator, HeuristicExtractor, HybridExtractor
from memory_system.dev import check_worker_database
from memory_system.embedding import EmbeddingClient
from memory_system.event_cipher import EventCipher
from memory_system.llm import ModelResponseTooLarge, OpenAICompatibleExtractor
from memory_system.models import (
    CandidateDecision,
    CorrectCommand,
    CorrectProcedureCommand,
    DoNotRememberCommand,
    EventSource,
    ForgetCommand,
    Identity,
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
    MemorySourcesChanged,
    NotFoundError,
    PostgresRepository,
    RateLimitExceeded,
)
from memory_system.retrieval import render_data_only
from memory_system.system import MemoryPolicyRejected, MemorySystem
from memory_system.worker import (
    backfill_embeddings,
    process_background_consolidation,
    process_one_extraction,
)


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
                "TRUNCATE memory_consolidation_opt_in, memory_write_rate_limit, "
                "command_dedup, deletion_job, "
                "memory_audit, memory_outbox, "
                "memory_recall_diagnostic, memory_candidate_decision, memory_relation, "
                "memory_evidence, memory_item, memory_event RESTART IDENTITY CASCADE"
            )
    finally:
        await repository.close()
    yield


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_background_consolidation_consent_is_scoped_and_revocable() -> None:
    app = PostgresRepository.from_url(_test_app_database_url())
    worker = PostgresRepository.from_url(_test_role_url("MEMORY_TEST_WORKER_DATABASE_URL"))
    scope = MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a")
    other = scope.model_copy(update={"user_id": "user-b"})
    try:
        await app.ping()
        await worker.ping()
        async with worker.background_consolidation_lock() as acquired:
            assert acquired
            async with worker.background_consolidation_lock() as second:
                assert not second
        async with worker.background_consolidation_lock() as reacquired:
            assert reacquired
        await app.set_background_consolidation(scope, True)
        await app.set_background_consolidation(scope, True)
        await app.set_background_consolidation(other, False)
        async with app.engine.begin() as connection:
            await app._set_scope(connection, other)
            assert (await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_consolidation_opt_in"
            )).scalar_one() == 0
        async with worker.engine.begin() as connection:
            assert (await connection.exec_driver_sql(
                "SELECT user_id FROM memory_consolidation_opt_in"
            )).scalar_one() == scope.user_id
        await app.set_background_consolidation(scope, False)
        await app.set_background_consolidation(scope, False)
        async with worker.engine.begin() as connection:
            assert (await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_consolidation_opt_in"
            )).scalar_one() == 0
    finally:
        await app.close()
        await worker.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_deletion_canary_rejects_stale_consolidation_run() -> None:
    owner = PostgresRepository.from_url(_test_database_url())
    worker = PostgresRepository.from_url(_test_role_url("MEMORY_TEST_WORKER_DATABASE_URL"))
    scope = MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a")
    try:
        await owner.set_background_consolidation(scope, True)
        source_ids = [
            (await owner.record_event(InteractionEvent(
                **scope.model_dump(), thread_id="thread-a", idempotency_key=f"event-{index}",
                source=EventSource.USER, content="项目进度", occurred_at=datetime.now(UTC),
            ))).event_id
            for index in range(2)
        ]
        _, job_id = await owner.prohibit_events(scope, source_ids[:1])
        assert job_id is not None
        async with owner.engine.begin() as connection:
            # Simulate residue left behind by a failed source-cleanup hook.
            await connection.exec_driver_sql(
                """
                INSERT INTO memory_consolidation_run
                    (tenant_id, user_id, agent_id, source_digest, source_ids)
                VALUES (%s, %s, %s, %s, %s::uuid[])
                """,
                (scope.tenant_id, scope.user_id, scope.agent_id, "stale", source_ids),
            )
            await connection.exec_driver_sql(
                "CREATE FUNCTION test_retain_consolidation_run() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$"
            )
            await connection.exec_driver_sql(
                "CREATE TRIGGER test_retain_consolidation_run BEFORE DELETE ON memory_consolidation_run "
                "FOR EACH ROW EXECUTE FUNCTION test_retain_consolidation_run()"
            )
        try:
            with pytest.raises(DeletionCanaryFailed):
                await worker.process_one_deletion()
            assert (await owner.deletion_status(scope, job_id)).status == "retry"
        finally:
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql("DROP FUNCTION test_retain_consolidation_run() CASCADE")
                await connection.exec_driver_sql(
                    "UPDATE deletion_job SET available_at=now() WHERE id=%s", (job_id,)
                )
        assert await worker.process_one_deletion()
        assert (await owner.deletion_status(scope, job_id)).status == "completed"
    finally:
        await worker.close()
        await owner.close()


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("ordinal", [0, 1], ids=["episode", "procedure"])
async def test_background_consolidation_only_discovers_new_authorized_sources(ordinal: int) -> None:
    app = PostgresRepository.from_url(_test_app_database_url())
    worker = PostgresRepository(create_async_engine(
        _test_role_url("MEMORY_TEST_WORKER_DATABASE_URL"),
        pool_size=2, max_overflow=0, pool_timeout=3,
    ))
    scope = MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a")

    async def record(key: str, *, allowed: bool = True, content: str = "项目进度更新"):
        return await app.record_event(InteractionEvent(
            **scope.model_dump(), thread_id="thread-a", idempotency_key=key,
            source=EventSource.USER, content=content, occurred_at=datetime.now(UTC),
            allow_memory_extraction=allowed,
        ))

    try:
        before = await record("before-consent")
        await app.set_background_consolidation(scope, True)
        await record("write-disabled", allowed=False)
        await record("opted-out", content="请不要保存这条项目进度")
        allowed = await record("after-consent")
        async with worker.engine.begin() as connection:
            await connection.exec_driver_sql(
                "DELETE FROM memory_outbox WHERE event_id=%s", (allowed.event_id,)
            )
        assert await worker.discover_background_consolidation_batches() == []
        second = await record("second-after-consent")
        ids = [allowed.event_id, second.event_id]
        assert not await worker.background_consolidation_completed(scope, ids)
        assert not await worker.complete_background_consolidation(scope, [before.event_id, ids[1]])
        draft = MemoryCandidate(
            statement="整理了两条项目进度", proposed_layer=MemoryLayer.EPISODIC,
            kind="episode", subject_key=None, valid_from=None, valid_to=None,
            expires_at=datetime.now(UTC) + timedelta(days=7),
            source_event_ids=ids, confidence=0.9, future_utility="medium",
            durability="uncertain", affect=None, sensitivity="low", poisoning_flags=[],
        )
        assert await worker.complete_background_consolidation(scope, ids, drafts=[draft])
        async with worker.engine.connect() as connection:
            saved = (await connection.exec_driver_sql(
                "SELECT drafts FROM memory_consolidation_run"
            )).scalar_one()
        assert saved == [draft.model_dump(mode="json")]
        other_ids = [before.event_id, second.event_id]
        assert not await worker.complete_background_consolidation(scope, other_ids)
        third = await record("third-after-consent")
        another_ids = [second.event_id, third.event_id]
        another_draft = draft.model_copy(update={"source_event_ids": another_ids})
        assert await worker.complete_background_consolidation(scope, another_ids, drafts=[another_draft])
        page = await app.list_background_consolidation_drafts(scope, limit=1)
        assert len(page.batches) == 1 and page.next_cursor == page.batches[0].source_digest
        tail = await app.list_background_consolidation_drafts(scope, cursor=page.next_cursor, limit=1)
        assert len(tail.batches) == 1 and tail.next_cursor is None
        assert {tuple(batch.drafts[0].source_event_ids) for batch in page.batches + tail.batches} == {
            tuple(ids), tuple(another_ids),
        }
        assert (await app.list_background_consolidation_drafts(
            scope.model_copy(update={"user_id": "another-user"})
        )).batches == []
        digest = worker.background_source_digest(another_ids)
        await app.dismiss_background_consolidation_drafts(
            scope.model_copy(update={"user_id": "another-user"}), digest
        )
        assert len((await app.list_background_consolidation_drafts(scope)).batches) == 2
        await app.dismiss_background_consolidation_drafts(scope, digest)
        await app.dismiss_background_consolidation_drafts(scope, digest)
        assert len((await app.list_background_consolidation_drafts(scope)).batches) == 1
        assert await worker.background_consolidation_completed(scope, another_ids)
        assert await worker.background_consolidation_completed(scope, ids[::-1])
        assert not await worker.complete_background_consolidation(scope, ids[::-1])
        assert len(await worker.consolidation_events(scope, ids, background=True)) == 2
        with pytest.raises(NotFoundError):
            await worker.consolidation_events(
                scope, [before.event_id, second.event_id], background=True
            )
        assert await worker.discover_background_consolidation_batches() == [
            (scope, sorted([*ids, third.event_id]))
        ]
        loop = asyncio.get_running_loop()

        def revoke_during_model_call(request):
            asyncio.run_coroutine_threadsafe(
                app.set_background_consolidation(scope, False), loop
            ).result(timeout=3)
            return Response(200, json={"choices": [{"message": {"content": '{"candidates":[]}'}}]})

        with Client(transport=MockTransport(revoke_during_model_call)) as http:
            model = OpenAICompatibleExtractor("http://model.test/v1", "summarizer", None, client=http)
            system = MemorySystem(worker, MemoryPolicy(), 2048, consolidation_extractor=model)
            with pytest.raises(NotFoundError):
                await system.propose_consolidation(scope, ids, background=True)
        assert not await worker.background_consolidation_completed(scope, ids)
        assert (await app.list_background_consolidation_drafts(scope)).batches == []
        assert not await worker.complete_background_consolidation(scope, ids)
        with pytest.raises(NotFoundError):
            await worker.consolidation_events(scope, ids, background=True)
        assert await worker.discover_background_consolidation_batches() == []
        await app.set_background_consolidation(scope, True)
        cited_ids = [(await record(key)).event_id for key in ("fresh-first", "fresh-second")]
        uncited = await record("unrelated", content="今天换了一把雨伞")
        fresh_ids = [*cited_ids, uncited.event_id]
        assert await worker.complete_background_consolidation(scope, fresh_ids, retry=True)
        assert not await worker.background_consolidation_completed(scope, fresh_ids)
        assert await worker.discover_background_consolidation_batches() == []
        owner = PostgresRepository.from_url(_test_database_url())
        try:
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_consolidation_run SET retry_at=now() - interval '1 second'"
                )
        finally:
            await owner.close()
        assert await worker.discover_background_consolidation_batches() == [(scope, sorted(fresh_ids))]
        requests = []

        def return_draft(request):
            requests.append(request)
            result = draft.model_copy(update={"source_event_ids": ["1", "2"]})
            if b"Propose at most three reusable procedures" in request.content:
                result = result.model_copy(update={
                    "statement": "整理项目进度时先核对原始记录",
                    "proposed_layer": MemoryLayer.PROCEDURAL, "kind": "procedure_proposal",
                    "durability": "stable", "expires_at": None,
                })
            return Response(200, json={"choices": [{"message": {"content":
                json.dumps({"candidates": [result.model_dump(mode="json")]})
            }}]})

        with Client(transport=MockTransport(return_draft)) as http:
            model = OpenAICompatibleExtractor("http://model.test/v1", "summarizer", None, client=http)
            system = MemorySystem(worker, MemoryPolicy(), 2048, consolidation_extractor=model)
            deletion_scope = scope.model_copy(update={"user_id": "another-user"})
            to_delete = await app.record_event(InteractionEvent(
                **deletion_scope.model_dump(), thread_id="delete", idempotency_key="delete",
                source=EventSource.USER, content="临时事项", occurred_at=datetime.now(UTC),
            ))
            _, deletion_job = await app.prohibit_events(deletion_scope, [to_delete.event_id])
            assert deletion_job is not None
            async with asyncio.timeout(10):
                _, extracted, deleted = await asyncio.gather(
                    process_background_consolidation(system),
                    process_one_extraction(worker, Consolidator(HeuristicExtractor(), MemoryPolicy())),
                    worker.process_one_deletion(),
                )
            assert extracted and deleted
            assert (await app.deletion_status(deletion_scope, deletion_job)).status == "completed"
            await process_background_consolidation(system)
        assert len(requests) == 2
        assert b"Summarize a coherent episode supported by multiple events" in requests[0].content
        assert await worker.background_consolidation_completed(scope, fresh_ids)
        assert await worker.discover_background_consolidation_batches() == []
        async with worker.engine.connect() as connection:
            saved = (await connection.exec_driver_sql(
                "SELECT drafts FROM memory_consolidation_run"
            )).scalar_one()
        assert [item["proposed_layer"] for item in saved] == ["L2", "L4"]
        assert all(set(item["source_event_ids"]) == set(cited_ids) for item in saved)
        assert not await app.list_memories(MemoryListQuery(scope=scope))
        confirmed_draft = MemoryCandidate.model_validate(saved[ordinal])
        confirmation = await record("confirm-draft", allowed=False, content="确认保存这条项目进度")
        reviewer = MemorySystem(app, MemoryPolicy(), 2048)
        digest = worker.background_source_digest(fresh_ids)

        def authenticate():
            return Identity(**scope.model_dump(), allowed_purposes={"review"}, memory_write=True)

        @asynccontextmanager
        async def lifespan(_app):
            yield

        api = create_app(reviewer, authenticate, lifespan)
        path = f"/v1/memory/consolidations/background/{digest}/confirm"
        body = {
            "ordinal": ordinal, "draft": confirmed_draft.model_dump(mode="json"),
            "source_event_id": confirmation.event_id,
        }
        headers = {"Idempotency-Key": "confirm"}
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            altered = {**body, "draft": {**body["draft"], "statement": "被篡改的草稿"}}
            assert (await client.post(path, json=altered, headers=headers)).status_code == 403
            confirmed = await client.post(path, json=body, headers=headers)
            assert confirmed.status_code == 200
            assert confirmed.json()["status"] == "completed"
            replay = await client.post(path, json=body, headers=headers)
            assert replay.status_code == 200 and replay.json() == confirmed.json()
        assert (await app.list_background_consolidation_drafts(scope)).batches == []
        memory_id, = confirmed.json()["affected_memory_ids"]
        items = await app.list_memories(MemoryListQuery(scope=scope))
        assert [(item.memory_id, item.layer) for item in items] == [
            (memory_id, confirmed_draft.proposed_layer)
        ]
        unrelated_withdrawal = await reviewer.execute_command_idempotent(
            DoNotRememberCommand(action="do_not_remember", scope=scope, event_ids=[uncited.event_id]),
            "withdraw-unrelated-source", "withdraw-unrelated-source",
        )
        assert unrelated_withdrawal.affected_memory_ids == []
        assert [item.memory_id for item in await app.list_memories(MemoryListQuery(scope=scope))] == [
            memory_id
        ]
        assert not await worker.background_consolidation_completed(scope, fresh_ids)
        withdrawal = await reviewer.execute_command_idempotent(
            DoNotRememberCommand(action="do_not_remember", scope=scope, event_ids=[cited_ids[0]]),
            "withdraw-background-source", "withdraw-background-source",
        )
        assert withdrawal.affected_memory_ids == [memory_id]
        assert not await app.list_memories(MemoryListQuery(scope=scope))

        cross_thread_ids = []
        now = datetime.now(UTC)
        for thread_id in ("delivery-plan", "delivery-review"):
            event = InteractionEvent(
                **scope.model_dump(), thread_id=thread_id, idempotency_key=thread_id,
                source=EventSource.USER, content="交付前核对项目计划",
                occurred_at=datetime.now(UTC), allow_memory_extraction=True,
            )
            event_id = (await app.record_event(event)).event_id
            cross_thread_ids.append(event_id)
            await worker.store_candidates(event_id, event, [draft.model_copy(update={
                "subject_key": "project:delivery", "source_event_ids": [event_id],
            })], "test-policy")
            await worker.store_candidates(event_id, event, [draft.model_copy(update={
                "statement": "发布前检查交付计划",
                "subject_key": "project:release", "source_event_ids": [event_id],
            })], "test-policy")
            for label, validity in (
                ("future", {"valid_from": now + timedelta(days=1)}),
                ("ended", {"valid_to": now - timedelta(days=1)}),
            ):
                await worker.store_candidates(event_id, event, [draft.model_copy(update={
                    "statement": f"项目进度 {label}", "subject_key": f"project:{label}",
                    "source_event_ids": [event_id], **validity,
                })], "test-policy")
        single_thread_ids = [cited_ids[1], (await record("later-event")).event_id]
        assert await worker.discover_background_consolidation_batches() == [
            (scope, sorted(cross_thread_ids)), (scope, sorted(single_thread_ids)),
        ]
        assert await worker.complete_background_consolidation(scope, cross_thread_ids)
        assert await worker.discover_background_consolidation_batches() == [
            (scope, sorted(single_thread_ids))
        ]
        assert await worker.complete_background_consolidation(scope, single_thread_ids)
        assert await worker.discover_background_consolidation_batches() == []
        semantic_ids = []
        semantic_events = []
        for index, statement in enumerate(("复核交付清单", "检查发版清单")):
            event = InteractionEvent(
                **scope.model_dump(), thread_id=f"semantic-{index}", idempotency_key=f"semantic-{index}",
                source=EventSource.USER, content=statement, occurred_at=datetime.now(UTC),
                allow_memory_extraction=True,
            )
            event_id = (await app.record_event(event)).event_id
            semantic_ids.append(event_id)
            semantic_events.append(event)
            memory_id, = await worker.store_candidates(event_id, event, [draft.model_copy(update={
                "statement": statement, "source_event_ids": [event_id],
            })], "test-policy")
            vector = [1.0, 0.0] if index == 0 else [0.0, 1.0]
            assert await worker.store_embedding(memory_id, statement, "semantic-test", vector)
        options = {"embedding_model": "semantic-test", "embedding_dimensions": 2}
        assert await worker.discover_background_consolidation_batches(**options) == []
        assert await worker.store_embedding(memory_id, statement, "semantic-test", [0.99, 0.01])
        additional = [
            draft.model_copy(update={
                "statement": f"发版清单条目 {number}", "source_event_ids": [event_id],
            })
            for number in range(32)
        ]
        additional_ids = await worker.store_candidates(event_id, event, additional, "test-policy")
        for extra_id, candidate in zip(additional_ids, additional, strict=True):
            assert await worker.store_embedding(
                extra_id, candidate.statement, "semantic-test", [0.99, 0.01]
            )
        assert await worker.discover_background_consolidation_batches(**options) == [
            (scope, sorted(semantic_ids))
        ]
        assert await worker.complete_background_consolidation(scope, semantic_ids)
        assert await worker.discover_background_consolidation_batches(**options) == []
        assert await worker.discover_background_consolidation_batches(
            embedding_model="different-model", embedding_dimensions=2
        ) == []
        shared_statement = "交付前统一核对清单"
        shared_ids = []
        for source_id, source_event in zip(semantic_ids, semantic_events, strict=True):
            shared_id, = await worker.store_candidates(source_id, source_event, [draft.model_copy(
                update={"statement": shared_statement, "source_event_ids": [source_id]}
            )], "test-policy")
            shared_ids.append(shared_id)
        assert shared_ids[0] == shared_ids[1]
        assert await worker.store_embedding(shared_ids[0], shared_statement, "shared-only", [1.0, 0.0])
        assert await worker.discover_background_consolidation_batches(
            embedding_model="shared-only", embedding_dimensions=2
        ) == []
    finally:
        await app.close()
        await worker.close()


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "opt_out", [
        "请不要记住", "请勿\u200b保存", "请不要再保存", "麻烦你别继续记录",
        "Do not remember this", "Please don’t store this in memory",
    ],
)
async def test_explicit_opt_out_blocks_evidence_but_allows_erasure(
    legacy: bool, opt_out: str
) -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a")
    try:
        receipt = await repository.record_event(
            InteractionEvent(
                **scope.model_dump(),
                thread_id="thread-a",
                idempotency_key="opt-out",
                source=EventSource.USER,
                content=f"{opt_out}：我非常焦虑，提醒我明天下午参加会议。",
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
@pytest.mark.parametrize(
    "schema_state", ["current", "hidden", "before_rotation_retry", "before_drafts", "before_draft_retry"]
)
async def test_api_database_readiness_checks_visible_schema(schema_state: str) -> None:
    old_revision = {
        "before_rotation_retry": "20261106_0017",
        "before_drafts": "20261113_0033",
        "before_draft_retry": "20261113_0037",
    }.get(schema_state)
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
        if old_revision is not None:
            root = Path(__file__).resolve().parents[1]
            config = Config(root / "alembic.ini")
            config.set_main_option("script_location", str(root / "migrations"))
            config.set_main_option("sqlalchemy.url", _test_database_url())
            worker_url = _test_role_url("MEMORY_TEST_WORKER_DATABASE_URL")
            command.downgrade(config, old_revision)
        async with repository.engine.connect() as connection:
            assert (await connection.exec_driver_sql("SELECT 1")).scalar_one() == 1
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health/ready")
            if old_revision is not None:
                with pytest.raises((UndefinedColumn, UndefinedTable)):
                    check_worker_database(worker_url)
                command.upgrade(config, "head")
                check_worker_database(worker_url)
                assert (await client.get("/health/ready")).status_code == 200
    finally:
        await repository.close()
        if old_revision is not None:
            _upgrade_test_database(_test_database_url())
    api_ready = schema_state == "current"
    assert response.status_code == (200 if api_ready else 503)
    if not api_ready:
        assert response.json() == {"code": "memory_unavailable"}


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_new_user_event_renews_expired_working_memory() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    worker = PostgresRepository.from_url(_test_role_url("MEMORY_TEST_WORKER_DATABASE_URL"))
    scope = MemoryScope(tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a")
    source = InteractionEvent(
        **scope.model_dump(), thread_id="thread-a", idempotency_key="working-state",
        source=EventSource.USER, content="我现在压力非常大", occurred_at=datetime.now(UTC),
        allow_memory_extraction=True,
    )
    consolidator = Consolidator(HeuristicExtractor(), MemoryPolicy())
    try:
        first_event = await repository.record_event(source)
        assert await process_one_extraction(worker, consolidator)
        initial, = await repository.list_memories(MemoryListQuery(scope=scope))
        assert initial.layer == MemoryLayer.WORKING
        async with worker.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE memory_item SET expires_at=now()-interval '1 hour' WHERE id=%s",
                (initial.memory_id,),
            )
        await worker.run_maintenance(30)
        assert not await repository.list_memories(MemoryListQuery(scope=scope))
        historical_expired = await repository.search(
            scope, source.content, "history", datetime.now(UTC)
        )
        assert [item.memory_id for item in historical_expired] == [initial.memory_id]
        assert historical_expired[0].affect == initial.affect
        assert not await repository.search(scope, source.content, "support", datetime.now(UTC))
        before_renewal = datetime.now(UTC)
        latest = source.model_copy(update={
            "idempotency_key": "working-state-renewed",
            "occurred_at": before_renewal + timedelta(seconds=1),
        })
        latest_event = await repository.record_event(latest)
        assert await process_one_extraction(worker, consolidator)
        renewed, = await repository.list_memories(MemoryListQuery(scope=scope))
        assert renewed.memory_id == initial.memory_id
        assert renewed.expires_at > datetime.now(UTC)
        assert len(renewed.source_event_ids) == 2
        assert renewed.affect is not None
        assert renewed.affect.observed_at == latest.occurred_at
        late_arrival = source.model_copy(update={
            "idempotency_key": "working-state-late-arrival",
            "occurred_at": source.occurred_at - timedelta(hours=1),
        })
        await repository.record_event(late_arrival)
        assert await process_one_extraction(worker, consolidator)
        after_late_arrival, = await repository.list_memories(MemoryListQuery(scope=scope))
        assert after_late_arrival.affect == renewed.affect
        assert after_late_arrival.expires_at == renewed.expires_at
        context = await MemorySystem(repository, MemoryPolicy(), 2048).build_context(
            RecallRequest(
                **scope.model_dump(), thread_id=source.thread_id,
                query=source.content, purpose="support", token_budget=1024,
                now=latest.occurred_at + timedelta(seconds=1),
            )
        )
        recalled, = context.items
        assert recalled.affect == renewed.affect
        assert recalled.expires_at == renewed.expires_at
        assert '"affect_observation":' in context.rendered_text
        assert recalled.affect.model_dump(mode="json")["observed_at"] in context.rendered_text
        historical = await repository.search(
            scope, source.content, "history", before_renewal
        )
        historical_item, = historical
        assert historical_item.affect == initial.affect
        assert historical_item.expires_at == initial.expires_at
        assert historical_item.source_event_ids == [first_event.event_id]
        assert len(recalled.source_event_ids) == 3
        owner = PostgresRepository.from_url(_test_database_url())
        valid_id = str(uuid4())
        try:
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_affect_observation SET expires_at=%s WHERE event_id=%s",
                    (before_renewal - timedelta(seconds=1), first_event.event_id),
                )
            assert not await repository.search(scope, source.content, "support", before_renewal)
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    """
                    WITH inserted AS (
                        INSERT INTO memory_item (
                            id, tenant_id, user_id, agent_id, statement, layer, kind, status,
                            confidence, policy_version, created_at, updated_at, expires_at
                        )
                        SELECT CASE WHEN n=0 THEN %s::uuid ELSE gen_random_uuid() END,
                               m.tenant_id, m.user_id, m.agent_id, '支持场景回归', 'L1', 'test',
                               'active', 1, 'test', m.created_at,
                               m.created_at + make_interval(secs => n), m.expires_at
                        FROM memory_item m CROSS JOIN generate_series(0, 20) AS n
                        WHERE m.id=%s
                        RETURNING id
                    )
                    INSERT INTO memory_affect_observation (
                        memory_id, event_id, affect, observed_at, expires_at, recorded_at
                    )
                    SELECT i.id, o.event_id, o.affect, o.observed_at, o.expires_at, o.recorded_at
                    FROM inserted i CROSS JOIN memory_affect_observation o
                    WHERE i.id<>%s AND o.memory_id=%s AND o.event_id=%s
                    """,
                    (valid_id, initial.memory_id, valid_id, initial.memory_id, first_event.event_id),
                )
                await connection.exec_driver_sql(
                    """
                    INSERT INTO memory_relation (
                        tenant_id, user_id, agent_id, from_memory_id, to_memory_id,
                        relation, confidence, created_at
                    )
                    SELECT tenant_id, user_id, agent_id, id, %s, 'contradicts', 1, created_at
                    FROM memory_item WHERE id=%s
                    """,
                    (initial.memory_id, valid_id),
                )
            fallback = await repository.search(scope, "unrelated", "support", before_renewal)
            assert [item.memory_id for item in fallback] == [valid_id]
            assert fallback[0].conflict_memory_ids == []
        finally:
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "DELETE FROM memory_item WHERE tenant_id=%s AND statement='支持场景回归'",
                    (scope.tenant_id,),
                )
                await connection.exec_driver_sql(
                    "UPDATE memory_affect_observation SET expires_at=%s WHERE event_id=%s",
                    (initial.expires_at, first_event.event_id),
                )
            await owner.close()
        observations = await repository.affect_history(scope, initial.memory_id, 50)
        earlier = await repository.affect_history(scope, initial.memory_id, 50, before_renewal)
        assert [item.event_id for item in earlier] == [first_event.event_id]
        async with worker.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE memory_item SET status='quarantined' WHERE id=%s",
                (initial.memory_id,),
            )
        assert not await repository.affect_history(scope, initial.memory_id, 50)
        async with worker.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE memory_item SET status='active' WHERE id=%s",
                (initial.memory_id,),
            )
        assert [item.observed_at for item in observations] == [
            latest.occurred_at, source.occurred_at, late_arrival.occurred_at,
        ]
        foreign = await repository.record_event(source.model_copy(update={
            "user_id": "other-user", "allow_memory_extraction": False,
        }))
        with pytest.raises(DBAPIError, match="memory_affect_source_scope"):
            async with worker.engine.begin() as connection:
                await connection.exec_driver_sql(
                    """
                    INSERT INTO memory_affect_observation
                        (memory_id, event_id, affect, observed_at, expires_at)
                    SELECT memory_id, %s, affect, observed_at, expires_at
                    FROM memory_affect_observation WHERE memory_id=%s AND event_id=%s
                    """,
                    (foreign.event_id, initial.memory_id, first_event.event_id),
                )
        privileged_history = await worker.affect_history(scope, initial.memory_id, 50)
        assert len(privileged_history) == 3
        assert foreign.event_id not in {item.event_id for item in privileged_history}
        assert not await repository.affect_history(
            scope.model_copy(update={"user_id": "other-user"}), initial.memory_id, 50
        )
        episode_id = None
        for receipt, event in ((latest_event, latest), (first_event, source)):
            candidate, = HeuristicExtractor().extract(event)
            episode_id, = await worker.store_candidates(
                receipt.event_id, event,
                [candidate.model_copy(update={"proposed_layer": MemoryLayer.EPISODIC})],
                MemoryPolicy.version,
            )
        affected, job_id = await repository.prohibit_events(scope, [latest_event.event_id])
        assert affected == []
        assert len(await repository.affect_history(scope, initial.memory_id, 50)) == 2
        remaining = await repository.list_memories(MemoryListQuery(scope=scope))
        remaining_state = next(item for item in remaining if item.memory_id == initial.memory_id)
        episode = next(item for item in remaining if item.memory_id == episode_id)
        assert episode.affect is None
        assert not await repository.affect_history(scope, episode.memory_id, 50)
        async with worker.engine.connect() as connection:
            result = await connection.exec_driver_sql(
                "SELECT affect, EXISTS (SELECT 1 FROM memory_affect_observation "
                "WHERE memory_id=m.id) FROM memory_item m WHERE id=%s",
                (episode_id,),
            )
            assert tuple(result.one()) == (None, False)
        with pytest.raises(DBAPIError, match="fk_memory_affect_working_layer"):
            async with worker.engine.begin() as connection:
                await connection.exec_driver_sql(
                    """
                    INSERT INTO memory_affect_observation
                        (memory_id, event_id, affect, observed_at, expires_at)
                    SELECT %s, event_id, affect, observed_at, expires_at
                    FROM memory_affect_observation WHERE memory_id=%s AND event_id=%s
                    """,
                    (episode_id, initial.memory_id, first_event.event_id),
                )
        candidate, = HeuristicExtractor().extract(source)
        assert candidate.affect is not None
        mismatched = candidate.affect.model_copy(
            update={"observed_at": source.occurred_at + timedelta(hours=1)}
        )
        invalid_id, = await worker.store_candidates(
            first_event.event_id, source,
            [candidate.model_copy(update={"kind": "invalid_observation", "affect": mismatched})],
            MemoryPolicy.version,
        )
        invalid_items = await repository.list_memories(MemoryListQuery(scope=scope))
        assert next(item for item in invalid_items if item.memory_id == invalid_id).affect is None
        assert not await repository.affect_history(scope, invalid_id, 50)
        assert remaining_state.affect.observed_at == source.occurred_at
        assert remaining_state.expires_at == initial.expires_at
        assert await worker.process_one_deletion()
        assert (await repository.deletion_status(scope, job_id)).status == "completed"
        async with worker.engine.connect() as connection:
            assert (await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_affect_observation WHERE event_id=%s",
                (latest_event.event_id,),
            )).scalar_one() == 0
        await repository.isolate_memories(scope, [initial.memory_id])
        assert await worker.process_one_deletion()
        async with worker.engine.connect() as connection:
            assert (await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_affect_observation WHERE memory_id=%s",
                (initial.memory_id,),
            )).scalar_one() == 0
    finally:
        await repository.close()
        await worker.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_confirmed_summary_requires_all_evidence_in_current_scope() -> None:
    cipher = EventCipher(base64.b64encode(os.urandom(32)).decode())
    repository = PostgresRepository.from_url(_test_app_database_url(), event_cipher=cipher)
    worker = PostgresRepository.from_url(_test_role_url("MEMORY_TEST_WORKER_DATABASE_URL"))
    scope = MemoryScope(tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a")
    source = InteractionEvent(
        **scope.model_dump(), thread_id="thread-a", idempotency_key="confirm-summary",
        source=EventSource.USER, content="请记住：项目讨论结果是周五交付",
        occurred_at=datetime.now(UTC),
    )
    try:
        confirmation = await repository.record_event(source)
        first = await repository.record_event(source.model_copy(update={
            "idempotency_key": "discussion-one", "content": "项目计划周五交付",
            "occurred_at": datetime.fromisoformat("2026-11-04T00:30:00+08:00"),
        }))
        second = await repository.record_event(source.model_copy(update={
            "idempotency_key": "discussion-two", "content": "已确认周五交付",
        }))
        foreign = await repository.record_event(source.model_copy(update={
            "user_id": "other-user", "idempotency_key": "foreign",
        }))
        prohibited = await repository.record_event(source.model_copy(update={
            "idempotency_key": "prohibited", "content": "不要记住这件事",
        }))
        sensitive = await repository.record_event(source.model_copy(update={
            "idempotency_key": "sensitive-source", "content": "password=example-secret",
        }))
        await repository.record_event(source.model_copy(update={
            "idempotency_key": "assistant-source", "source": EventSource.ASSISTANT,
        }))
        future = await repository.record_event(source.model_copy(update={
            "idempotency_key": "future-source", "occurred_at": source.occurred_at + timedelta(days=1),
        }))
        assert set(await repository.discover_consolidation_sources(scope, source.thread_id)) == {
            confirmation.event_id, first.event_id, second.event_id, sensitive.event_id,
        }
        assert await repository.discover_consolidation_sources(scope, "other-thread") == []
        await repository.record_event(source.model_copy(update={
            "thread_id": "single-event", "idempotency_key": "single-event",
        }))
        assert await repository.discover_consolidation_threads(scope) == [source.thread_id]
        assert await repository.discover_consolidation_threads(
            scope.model_copy(update={"user_id": "other-user"})
        ) == []
        system = MemorySystem(repository, MemoryPolicy(), 2048)
        candidate = MemoryCandidate(
            statement="项目讨论结果是周五交付", proposed_layer=MemoryLayer.EPISODIC,
            kind="event_summary", subject_key="project:delivery", valid_from=source.occurred_at,
            valid_to=None, expires_at=source.occurred_at + timedelta(days=7),
            source_event_ids=["1", "2"], confidence=0.9, future_utility="medium",
            durability="uncertain", affect=None, sensitivity="low", poisoning_flags=[],
        )
        model_requests = []

        def summarize(request):
            model_requests.append(request)
            assert "项目计划周五交付".encode() in request.content
            assert b"2026-11-04T00:30:00+08:00" in request.content
            assert "已确认周五交付".encode() in request.content
            assert first.event_id.encode() not in request.content
            assert second.event_id.encode() not in request.content
            assert b"example-secret" not in request.content
            assert foreign.event_id.encode() not in request.content
            candidates = [candidate, candidate.model_copy(update={"source_event_ids": ["1", foreign.event_id]})]
            if b"reusable procedures" in request.content:
                procedure = candidate.model_copy(update={
                    "proposed_layer": MemoryLayer.PROCEDURAL, "kind": "procedure_proposal",
                    "statement": "交付前先核对计划与确认记录", "expires_at": None,
                })
                candidates += [
                    procedure, procedure.model_copy(update={"poisoning_flags": ["test_flag"]})
                ]
            return Response(200, json={"choices": [{"message": {"content":
                '{"candidates":[' + ",".join(item.model_dump_json() for item in candidates) + ']}'
            }}]})

        with Client(transport=MockTransport(summarize)) as http:
            system.consolidation_limit_per_minute = 1
            system.consolidation_extractor = OpenAICompatibleExtractor(
                "http://model.test/v1", "summarizer", None, client=http
            )
            with pytest.raises(NotFoundError):
                await system.propose_consolidation(scope, [first.event_id, foreign.event_id])
            with pytest.raises(NotFoundError):
                await system.propose_consolidation(scope, [first.event_id, future.event_id])
            with pytest.raises(MemoryPolicyRejected):
                await system.propose_consolidation(scope, [first.event_id, sensitive.event_id])
            assert not model_requests
            proposals = await system.propose_consolidation(
                scope, [first.event_id, second.event_id, sensitive.event_id], automatic_sources=True,
            )
            assert len(proposals) == 1
            assert set(proposals[0].source_event_ids) == {first.event_id, second.event_id}
            assert not await repository.list_memories(MemoryListQuery(scope=scope))
            replica = MemorySystem(
                repository, MemoryPolicy(), 2048,
                consolidation_extractor=system.consolidation_extractor,
                consolidation_limit_per_minute=1,
            )
            with pytest.raises(RateLimitExceeded) as limited:
                await replica.propose_consolidation(scope, [first.event_id, second.event_id])
            assert limited.value.operation == "consolidation"
            assert len(model_requests) == 1
            system.consolidation_limit_per_minute = 2
            procedure, = await system.propose_consolidation(
                scope, [first.event_id, second.event_id], kind="procedure"
            )
            assert procedure.proposed_layer is MemoryLayer.PROCEDURAL
            assert set(procedure.source_event_ids) == {first.event_id, second.event_id}
            assert not MemoryPolicy().evaluate_candidate(procedure, source).allowed
            assert not await repository.list_memories(MemoryListQuery(scope=scope))
        withdrawing = await repository.record_event(source.model_copy(update={
            "idempotency_key": "withdraw-during-preview", "content": "已确认周五交付",
        }))
        loop = asyncio.get_running_loop()

        def summarize_after_withdrawal(request):
            response = summarize(request)
            asyncio.run_coroutine_threadsafe(
                repository.prohibit_events(scope, [withdrawing.event_id]), loop
            ).result(timeout=5)
            return response

        with Client(transport=MockTransport(summarize_after_withdrawal)) as http:
            system.consolidation_limit_per_minute = 3
            system.consolidation_extractor = OpenAICompatibleExtractor(
                "http://model.test/v1", "summarizer", None, client=http
            )
            with pytest.raises(NotFoundError):
                await system.propose_consolidation(scope, [first.event_id, withdrawing.event_id])
            assert await worker.process_one_deletion()
            assert not await repository.list_memories(MemoryListQuery(scope=scope))
        summary = RememberCommand(
            action="remember", scope=scope, statement=proposals[0].statement,
            source_event_id=confirmation.event_id.upper(),
            supporting_event_ids=[first.event_id, second.event_id, first.event_id.upper()],
            expires_at=proposals[0].expires_at,
        )
        for invalid in (foreign.event_id, prohibited.event_id, confirmation.event_id, future.event_id):
            with pytest.raises(NotFoundError):
                await system.execute_command_idempotent(
                    summary.model_copy(update={"supporting_event_ids": [first.event_id, invalid]}),
                    "confirmed-summary", "summary-payload",
                )
            assert not await repository.list_memories(MemoryListQuery(scope=scope))
        for expiry in (datetime.now(UTC) - timedelta(seconds=1), datetime.now(UTC) + timedelta(days=181)):
            with pytest.raises(MemoryPolicyRejected, match="expiry_outside_policy_window"):
                await system.execute_command_idempotent(
                    summary.model_copy(update={"expires_at": expiry}),
                    "confirmed-summary", "summary-payload",
                )
            assert not await repository.list_memories(MemoryListQuery(scope=scope))
        receipt = await system.execute_command_idempotent(
            summary, "confirmed-summary", "summary-payload"
        )
        assert receipt == await system.execute_command_idempotent(
            summary, "confirmed-summary", "summary-payload"
        )
        stored, = await repository.list_memories(MemoryListQuery(scope=scope))
        assert stored.layer is MemoryLayer.EPISODIC
        assert stored.expires_at == proposals[0].expires_at
        assert not await repository.profile(scope, datetime.now(UTC))
        assert not await repository.search(
            scope, summary.statement, "task", stored.expires_at + timedelta(seconds=1)
        )
        assert set(stored.source_event_ids) == {
            confirmation.event_id, first.event_id, second.event_id,
        }
        for table in ("memory_dependency", "memory_evidence"):
            with pytest.raises(DBAPIError, match="row-level security"):
                async with repository.engine.begin() as connection:
                    await repository._set_scope(connection, scope)
                    await connection.exec_driver_sql(
                        f"INSERT INTO {table} (memory_id, event_id) VALUES (%s, %s)",
                        (stored.memory_id, foreign.event_id),
                    )
        correction_source = await repository.record_event(
            source.model_copy(update={"idempotency_key": "correct-summary-source"})
        )
        extra_source = await repository.record_event(
            source.model_copy(update={"idempotency_key": "additional-summary-source"})
        )

        async def wait_for_blocked_transaction(connection):
            while True:
                blocked = await connection.exec_driver_sql(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE NOT granted "
                    "AND pg_backend_pid()=ANY(pg_blocking_pids(pid)))"
                )
                if blocked.scalar_one():
                    return
                await asyncio.sleep(0.01)

        async def correct_with_stale_sources():
            with pytest.raises(MemorySourcesChanged):
                await repository.correct(
                    scope, stored.memory_id, "更新后的交付摘要",
                    correction_source.event_id, MemoryPolicy.version,
                )

        async with (
            asyncio.timeout(8),
            asyncio.TaskGroup() as tasks,
            repository.engine.begin() as connection,
        ):
            await repository._set_scope(connection, scope)
            await connection.exec_driver_sql(
                "SELECT id FROM memory_item WHERE id=%s FOR UPDATE", (stored.memory_id,)
            )
            await connection.exec_driver_sql(
                "INSERT INTO memory_dependency (memory_id, event_id) VALUES (%s, %s)",
                (stored.memory_id, extra_source.event_id),
            )
            tasks.create_task(correct_with_stale_sources())
            await wait_for_blocked_transaction(connection)

        async with worker.engine.begin() as connection:
            detached = await connection.exec_driver_sql(
                "DELETE FROM memory_evidence WHERE memory_id=%s AND event_id=%s",
                (stored.memory_id, first.event_id),
            )
            assert detached.rowcount == 1
        async with (
            asyncio.timeout(8),
            asyncio.TaskGroup() as tasks,
            repository.engine.begin() as connection,
        ):
            await repository._set_scope(connection, scope)
            corrected_id = await repository._correct(
                connection, scope, stored.memory_id, "更新后的交付摘要",
                correction_source.event_id, MemoryPolicy.version, procedural=False,
            )
            corrected_rows = await connection.exec_driver_sql(
                """
                SELECT m.expires_at, e.event_id::text FROM memory_item m
                JOIN memory_evidence e ON e.memory_id=m.id WHERE m.id=%s
                """,
                (corrected_id,),
            )
            assert set(corrected_rows) == {
                (stored.expires_at, event_id) for event_id in (
                    confirmation.event_id, first.event_id, second.event_id,
                    correction_source.event_id, extra_source.event_id,
                )
            }
            withdrawal = tasks.create_task(system.execute_command_idempotent(
                DoNotRememberCommand(
                    action="do_not_remember", scope=scope, event_ids=[first.event_id],
                ),
                "withdraw-summary-source", "withdraw-summary-source",
            ))
            await wait_for_blocked_transaction(connection)
        erased = withdrawal.result()
        assert set(erased.affected_memory_ids) == {stored.memory_id, corrected_id}
        assert not await repository.list_memories(MemoryListQuery(scope=scope))
        assert await worker.process_one_deletion()
        async with worker.engine.connect() as connection:
            assert (await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_dependency WHERE memory_id IN (%s, %s)",
                (stored.memory_id, corrected_id),
            )).scalar_one() == 0
        assert (await repository.deletion_status(scope, erased.deletion_job_id)).status == "completed"
    finally:
        await worker.close()
        await repository.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_chinese_recall_gold_across_threads_and_after_forget() -> None:
    reader = PostgresRepository.from_url(_test_app_database_url())
    worker = PostgresRepository.from_url(_test_role_url("MEMORY_TEST_WORKER_DATABASE_URL"))
    scope = MemoryScope(tenant_id=f"eval-{uuid4()}", user_id="user", agent_id="agent")
    foreign_scope = scope.model_copy(update={"tenant_id": f"eval-{uuid4()}"})
    system = MemorySystem(reader, MemoryPolicy(), 2048)
    before_ingestion = datetime.now(UTC)
    try:
        ids = {}
        for thread, statement, owner in (
            ("project", "青禾项目的交付时间是周五下午三点", scope),
            ("other", "白榆项目的交付时间是周一上午九点", scope),
            ("foreign", "青禾项目的交付时间是周日上午十点", foreign_scope),
        ):
            event = await reader.record_event(InteractionEvent(
                **owner.model_dump(), thread_id=thread, idempotency_key=thread,
                source=EventSource.USER, content=f"请记住：{statement}",
                occurred_at=datetime.now(UTC),
            ))
            ids[thread] = await reader.remember(
                owner, statement, event.event_id, MemoryPolicy.version
            )
        request = RecallRequest(
            **scope.model_dump(), thread_id="later-chat", query="青禾项目的交付时间",
            purpose="task", token_budget=256, now=datetime.now(UTC),
        )
        context = await system.build_context(request)
        assert context.items[0].memory_id == ids["project"]
        assert ids["foreign"] not in {item.memory_id for item in context.items}
        earlier = await system.build_context(
            request.model_copy(update={"purpose": "history", "now": before_ingestion})
        )
        assert earlier.items == []

        unrelated_project = await system.build_context(
            request.model_copy(update={"query": "白榆项目的交付时间"})
        )
        assert unrelated_project.items[0].memory_id == ids["other"]

        correction = await reader.record_event(InteractionEvent(
            **scope.model_dump(), thread_id="schedule-update", idempotency_key="schedule-update",
            source=EventSource.USER, content="青禾项目改到周六下午三点交付",
            occurred_at=datetime.now(UTC),
        ))
        replacement_id = await reader.correct(
            scope, ids["project"], "青禾项目的交付时间是周六下午三点",
            correction.event_id, MemoryPolicy.version,
        )
        latest_request = request.model_copy(update={"now": datetime.now(UTC)})
        latest = await system.build_context(latest_request)
        assert latest.items[0].memory_id == replacement_id
        assert ids["project"] not in {item.memory_id for item in latest.items}
        previous = await system.build_context(request.model_copy(update={"purpose": "history"}))
        assert previous.items[0].memory_id == ids["project"]
        assert replacement_id not in {item.memory_id for item in previous.items}

        _, deletion_id = await reader.isolate_memories(scope, [ids["project"]])
        assert deletion_id is not None
        assert await worker.process_one_deletion()
        historical = await system.build_context(
            latest_request.model_copy(update={"purpose": "history"})
        )
        assert {item.memory_id for item in historical.items}.isdisjoint(
            {ids["project"], ids["foreign"]}
        )
        assert historical.items[0].memory_id == replacement_id
    finally:
        await worker.close()
        await reader.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_correcting_explicit_episode_preserves_expiry() -> None:
    repository = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a")
    source = InteractionEvent(
        **scope.model_dump(), thread_id="thread-a", idempotency_key="confirmed-episode",
        source=EventSource.USER, content="请记住：本周项目周五交付", occurred_at=datetime.now(UTC),
    )
    expiry = datetime.now(UTC) + timedelta(days=7)
    system = MemorySystem(repository, MemoryPolicy(), 2048)
    try:
        event = await repository.record_event(source)
        receipt = await system.execute_command_idempotent(
            RememberCommand(
                action="remember", scope=scope, statement="本周项目周五交付",
                source_event_id=event.event_id, expires_at=expiry,
            ), "remember-episode", "remember-episode",
        )
        redacted = await repository.record_event(
            source.model_copy(update={"idempotency_key": "redacted-correction"})
        )
        owner = PostgresRepository.from_url(_test_database_url())
        try:
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_event SET content='[expired]', redacted_at=now() WHERE id=%s",
                    (redacted.event_id,),
                )
        finally:
            await owner.close()
        with pytest.raises(NotFoundError):
            await repository.remember(
                scope, "已擦除的来源", redacted.event_id, MemoryPolicy.version,
            )
        with pytest.raises(NotFoundError):
            await repository.correct(
                scope, receipt.affected_memory_ids[0], "无效纠正",
                redacted.event_id, MemoryPolicy.version,
            )
        corrected = await system.execute_command_idempotent(
            CorrectCommand(
                action="correct", scope=scope, memory_id=receipt.affected_memory_ids[0],
                replacement="本周项目周六交付", source_event_id=event.event_id,
            ), "correct-episode", "correct-episode",
        )
        active, = await repository.list_memories(MemoryListQuery(scope=scope))
        assert active.memory_id == corrected.affected_memory_ids[1]
        assert active.layer is MemoryLayer.EPISODIC
        assert active.expires_at == expiry
    finally:
        await repository.close()


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(("layer", "age_days"), [("L2", 45), ("L1", 2)])
async def test_time_sensitive_utility_decays_without_hiding_history(layer, age_days) -> None:
    owner = PostgresRepository.from_url(_test_database_url())
    reader = PostgresRepository.from_url(_test_app_database_url())
    scope = MemoryScope(tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a")
    old_id, recent_id = str(uuid4()), str(uuid4())
    try:
        async with owner.engine.begin() as connection:
            for memory_id, days, utility in ((old_id, age_days, "high"), (recent_id, 0, "medium")):
                await connection.exec_driver_sql(
                    """
                    INSERT INTO memory_item (
                        id, tenant_id, user_id, agent_id, statement, layer, kind,
                        status, confidence, policy_version, future_utility, created_at, valid_from
                    ) VALUES (
                        %s, %s, %s, %s, 'handover plan', %s, 'test',
                        'active', 1, 'test', %s,
                        now()-make_interval(days => %s), now()-make_interval(days => %s)
                    )
                    """,
                    (memory_id, scope.tenant_id, scope.user_id, scope.agent_id,
                     layer, utility, days, days),
                )
        now = datetime.now(UTC)
        current = await reader.search(scope, "handover", "task", now)
        history = await reader.search(scope, "handover", "history", now)
        assert [item.memory_id for item in current] == [recent_id, old_id]
        assert [item.memory_id for item in history] == [old_id, recent_id]
        if layer == "L2":
            await reader.record_recall_diagnostic(
                scope, str(uuid4()), "task", 512, 2, [old_id], False,
                reinforced_memory_ids=[old_id, recent_id], recalled_at=now,
            )
            async with owner.engine.connect() as connection:
                rows = await connection.exec_driver_sql(
                    "SELECT id::text, last_reinforced_at FROM memory_item WHERE id IN (%s,%s)",
                    (old_id, recent_id),
                )
                reinforced = dict(rows.all())
            assert reinforced[old_id] is not None
            assert reinforced[recent_id] is None
            await reader.record_recall_diagnostic(
                scope, str(uuid4()), "task", 512, 2, [old_id], False,
                reinforced_memory_ids=[old_id], recalled_at=datetime.now(UTC),
            )
            await reader.record_recall_diagnostic(
                scope, str(uuid4()), "task", 512, 2, [recent_id], False,
                reinforced_memory_ids=[recent_id], recalled_at=now - timedelta(days=1),
            )
            async with owner.engine.connect() as connection:
                rows = await connection.exec_driver_sql(
                    "SELECT id::text, last_reinforced_at FROM memory_item WHERE id IN (%s,%s)",
                    (old_id, recent_id),
                )
                assert dict(rows.all()) == reinforced
            assert [
                item.memory_id for item in await reader.search(scope, "handover", "task", now)
            ] == [recent_id, old_id]
            after_reinforcement = datetime.now(UTC)
            assert [
                item.memory_id
                for item in await reader.search(scope, "handover", "task", after_reinforcement)
            ] == [old_id, recent_id]
            assert [
                item.memory_id for item in await reader.search(
                    scope, "handover", "task", after_reinforcement + timedelta(days=28)
                )
            ] == [recent_id, old_id]
            assert [item.memory_id for item in await reader.search(scope, "handover", "history", now)] == [
                old_id, recent_id
            ]
            with pytest.raises(DBAPIError, match="ck_memory_item_affect_l1"):
                async with owner.engine.begin() as connection:
                    await connection.exec_driver_sql(
                        "UPDATE memory_item SET affect='{}'::jsonb WHERE id=%s", (old_id,)
                    )
            assert all(item.affect is None for item in await reader.search(
                scope, "handover", "history", datetime.now(UTC)
            ))
            assert all(item.affect is None for item in await reader.list_memories(
                MemoryListQuery(scope=scope)
            ))
        if layer == "L1":
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    """
                    UPDATE memory_item SET affect=jsonb_build_object(
                        'valence', -0.5, 'arousal', 0.7, 'labels', '["distress"]'::jsonb,
                        'confidence', 0.9, 'observed_at', now(), 'derived_from', 'text'
                    ) WHERE id=%s
                    """, (old_id,),
                )
            renewed = await reader.search(scope, "handover", "support", datetime.now(UTC))
            assert [item.memory_id for item in renewed] == [old_id, recent_id]
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_item SET affect=jsonb_set("
                    "affect, '{observed_at}', to_jsonb(now()+interval '1 day')) WHERE id=%s",
                    (old_id,),
                )
            before_observation = await reader.search(
                scope, "handover", "support", datetime.now(UTC)
            )
            assert [item.memory_id for item in before_observation] == [recent_id, old_id]
            observation = await reader.record_event(InteractionEvent(
                **scope.model_dump(), thread_id="affect", idempotency_key="affect-ranking",
                source=EventSource.USER, content="我现在很焦虑", occurred_at=datetime.now(UTC),
            ))
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    """
                    INSERT INTO memory_affect_observation (
                        memory_id, event_id, affect, observed_at, expires_at
                    )
                    SELECT %s, e.id,
                           jsonb_set(m.affect, '{observed_at}', to_jsonb(now()-interval '1 hour')),
                           now()-interval '1 hour', now()+interval '1 day'
                    FROM memory_event e JOIN memory_item m ON m.id=%s
                    WHERE e.id=%s
                    """,
                    (old_id, old_id, observation.event_id),
                )
                await connection.exec_driver_sql(
                    "UPDATE memory_item SET future_utility='medium' WHERE id=%s", (old_id,)
                )
            supported = await reader.search(scope, "handover", "support", datetime.now(UTC))
            assert [item.memory_id for item in supported] == [old_id, recent_id]
            assert supported[0].affect is not None
            task = await reader.search(scope, "handover", "task", datetime.now(UTC))
            assert [item.memory_id for item in task] == [recent_id, old_id]
            assert all(item.affect is None for item in task)
            assert "affect_observation" not in render_data_only(task)
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    """
                    INSERT INTO memory_item (
                        id, tenant_id, user_id, agent_id, statement, layer, kind, status,
                        confidence, policy_version, updated_at
                    )
                    SELECT gen_random_uuid(), tenant_id, user_id, agent_id, 'neutral entry',
                           layer, kind, status, confidence, policy_version, updated_at
                    FROM memory_item CROSS JOIN generate_series(1, 21) WHERE id=%s
                    """,
                    (recent_id,),
                )
            fallback = await reader.search(scope, "zzzzzz", "support", datetime.now(UTC))
            assert len(fallback) == 20
            assert fallback[0].memory_id == old_id
    finally:
        await reader.close()
        await owner.close()


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
        occurred_at=datetime.now(timezone(timedelta(hours=8))),
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
        model_requests = []

        def extract(request):
            model_requests.append(request.content)
            return Response(200, json={"choices": [{"message": {"content": '{"candidates":[]}'}}]})

        with Client(transport=MockTransport(extract)) as http:
            model = OpenAICompatibleExtractor("http://model.test/v1", "extractor", None, client=http)
            worked = await process_one_extraction(
                worker, Consolidator(HybridExtractor(HeuristicExtractor(), model), MemoryPolicy())
            )
        assert len(model_requests) == 1
        assert source.occurred_at.isoformat().encode() in model_requests[0]
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
        for magnitude in (1e300, 1e-300):
            extreme = MockTransport(lambda _, magnitude=magnitude: Response(
                200, json={"data": [{"index": 0, "embedding": [magnitude, 0]}]},
            ))
            with Client(base_url="http://embedding.test/v1/", transport=extreme) as http:
                embedder = EmbeddingClient(http, "test-model", 2)
                vector, = embedder.embed([memories[0].statement])
                assert await worker.store_embedding(
                    memories[0].memory_id, memories[0].statement, "test-model", vector
                )
                context = await MemorySystem(
                    repository, MemoryPolicy(), 2048, embedder
                ).build_context(request)
                assert context.items[0].memory_id == memories[0].memory_id
                assert context.items[0].query_matched
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
                    "SELECT content, ciphertext, occurred_utc_offset_seconds "
                    "FROM memory_event WHERE id=%s",
                    (receipt.event_id,),
                )).one()) == ("[deleted-by-user]", None, None)
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
        foreign = await repository.record_event(source.model_copy(update=scope_b.model_dump()))
        foreign_memory_id = await repository.remember(
            scope_b, "B 用户偏好", foreign.event_id, MemoryPolicy.version
        )
        insert_relation = """
            INSERT INTO memory_relation (
                tenant_id, user_id, agent_id, from_memory_id, to_memory_id,
                relation, confidence
            )
            VALUES (%s, %s, %s, %s, %s, 'contradicts', 1)
        """
        endpoint_pairs = [(memory_id, foreign_memory_id), (foreign_memory_id, memory_id)]
        for endpoints in endpoint_pairs:
            with pytest.raises(DBAPIError, match="row-level security"):
                async with repository.engine.begin() as connection:
                    await repository._set_scope(connection, scope_a)
                    await connection.exec_driver_sql(
                        insert_relation,
                        (scope_a.tenant_id, scope_a.user_id, scope_a.agent_id, *endpoints),
                    )
        privileged = PostgresRepository.from_url(_test_database_url())
        try:
            for endpoint, endpoints in zip(("to", "from"), endpoint_pairs, strict=True):
                with pytest.raises(DBAPIError, match=f"fk_memory_relation_{endpoint}_scope"):
                    async with privileged.engine.begin() as connection:
                        await connection.exec_driver_sql(
                            insert_relation,
                            (scope_a.tenant_id, scope_a.user_id, scope_a.agent_id, *endpoints),
                        )
        finally:
            await privileged.close()
        for invalid in (foreign.event_id, str(uuid4())):
            with pytest.raises(NotFoundError):
                await repository.prohibit_events(scope_a, [event_receipt.event_id, invalid])
            surviving, = await repository.list_memories(MemoryListQuery(scope=scope_a))
            assert surviving.source_event_ids == [event_receipt.event_id]
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
        async with repository.engine.begin() as connection:
            await connection.exec_driver_sql(
                """
                INSERT INTO memory_relation (
                    tenant_id, user_id, agent_id, from_memory_id, to_memory_id,
                    relation, confidence
                )
                VALUES (%s, %s, %s, %s, %s, 'supersedes', 1)
                """,
                (scope.tenant_id, scope.user_id, scope.agent_id, replacement_id, original_id),
            )
        _, job_id = await repository.isolate_memories(scope, [original_id])
        assert job_id is not None
        async with repository.engine.begin() as connection:
            await connection.exec_driver_sql(
                "CREATE FUNCTION test_retain_relation() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$"
            )
            await connection.exec_driver_sql(
                "CREATE TRIGGER test_retain_relation BEFORE DELETE ON memory_relation "
                "FOR EACH ROW EXECUTE FUNCTION test_retain_relation()"
            )
        try:
            with pytest.raises(DeletionCanaryFailed, match="deletion_canary_failed"):
                await repository.process_one_deletion()
            deletion = await repository.deletion_status(scope, job_id)
            assert deletion.status == "retry"
            async with repository.engine.connect() as connection:
                original = await connection.exec_driver_sql(
                    "SELECT status FROM memory_item WHERE id=%s", (original_id,)
                )
                assert original.scalar_one() == "deleting"
        finally:
            async with repository.engine.begin() as connection:
                await connection.exec_driver_sql("DROP FUNCTION test_retain_relation() CASCADE")
                await connection.exec_driver_sql(
                    "UPDATE deletion_job SET available_at=now() WHERE id=%s", (job_id,)
                )
        assert await repository.process_one_deletion() is True
        memories = await repository.list_memories(MemoryListQuery(scope=scope))
        async with repository.engine.connect() as connection:
            supersedes_id = await connection.exec_driver_sql(
                "SELECT supersedes_id FROM memory_item WHERE id=%s",
                (replacement_id,),
            )
            replacement_parent = supersedes_id.scalar_one()
            remaining_relations = await connection.exec_driver_sql(
                "SELECT count(*) FROM memory_relation WHERE from_memory_id=%s OR to_memory_id=%s",
                (original_id, original_id),
            )
            assert remaining_relations.scalar_one() == 0
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
        async with repository.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE memory_event SET prohibited_from_memory=true WHERE id=%s",
                (events[0].event_id,),
            )
        for items in (
            await repository.list_memories(MemoryListQuery(scope=scope)),
            await repository.search(scope, "简洁回答", "task", datetime.now(UTC)),
            await repository.profile(scope, datetime.now(UTC)),
        ):
            assert [item.source_event_ids for item in items] == [[events[1].event_id]]
        async with repository.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE memory_event SET prohibited_from_memory=false WHERE id=%s",
                (events[0].event_id,),
            )
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
            scope, [memory_ids[0].upper(), memory_ids[0]]
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
@pytest.mark.parametrize("bounded_pool", [False, True], ids=["default-pool", "single-connection"])
async def test_concurrent_worker_replicas_complete_each_event_once(bounded_pool: bool) -> None:
    app = PostgresRepository.from_url(_test_app_database_url())
    worker_url = _test_role_url("MEMORY_TEST_WORKER_DATABASE_URL")
    pool_options = {"pool_size": 1, "max_overflow": 0, "pool_timeout": 3} if bounded_pool else {}
    workers = [PostgresRepository(create_async_engine(worker_url, **pool_options)) for _ in range(2)]
    scope = MemoryScope(tenant_id="tenant-a", user_id="user-a", agent_id="agent-a")
    try:
        for index in range(16):
            await app.record_event(InteractionEvent(
                **scope.model_dump(), thread_id="replicas", idempotency_key=f"event-{index}",
                source=EventSource.USER, content=f"请记住：我的项目代号是测试{index}",
                occurred_at=datetime.now(UTC), allow_memory_extraction=True,
            ))
        async with asyncio.timeout(10):
            results = await asyncio.gather(*[
                process_one_extraction(worker, Consolidator(HeuristicExtractor(), MemoryPolicy()))
                for _ in range(8)
                for worker in workers
            ])
        assert all(results)
        assert not await process_one_extraction(
            workers[0], Consolidator(HeuristicExtractor(), MemoryPolicy())
        )
        async with workers[0].engine.connect() as connection:
            counts = await connection.exec_driver_sql("""
                SELECT count(*), count(DISTINCT event_id),
                       count(*) FILTER (WHERE status='completed' AND attempts=1)
                FROM memory_outbox WHERE tenant_id=%s AND user_id=%s AND agent_id=%s
            """, (scope.tenant_id, scope.user_id, scope.agent_id))
            assert tuple(counts.one()) == (16, 16, 16)
        assert len(await app.list_memories(MemoryListQuery(scope=scope))) == 16
    finally:
        for worker in workers:
            await worker.close()
        await app.close()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_only_one_replica_runs_maintenance_at_a_time() -> None:
    url = _test_database_url()
    repository = PostgresRepository.from_url(url)
    memory_id = str(uuid4())
    episodic_id = str(uuid4())
    try:
        source = InteractionEvent(
            tenant_id="tenant-a", user_id="user-a", agent_id="agent-a",
            thread_id="retention", idempotency_key="old-source", source=EventSource.USER,
            content="合成保留期样本", occurred_at=datetime.fromisoformat("2026-10-01T08:00:00+08:00"),
        )
        old_event = await repository.record_event(source)
        fresh_event = await repository.record_event(
            source.model_copy(update={"idempotency_key": "fresh-source"})
        )
        async with repository.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE memory_event SET recorded_at=now()-interval '31 days' WHERE id=%s",
                (old_event.event_id,),
            )
            await connection.exec_driver_sql(
                """
                INSERT INTO memory_item (
                    id, tenant_id, user_id, agent_id, statement, layer, kind,
                    status, confidence, policy_version, expires_at
                ) VALUES (%s, 'tenant-a', 'user-a', 'agent-a', '我现在压力非常大', 'L1',
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
            events = await observer.exec_driver_sql(
                "SELECT id::text, occurred_utc_offset_seconds, redacted_at IS NOT NULL "
                "FROM memory_event WHERE id IN (%s, %s)",
                (old_event.event_id, fresh_event.event_id),
            )
            assert {row[0]: tuple(row[1:]) for row in events} == {
                old_event.event_id: (None, True),
                fresh_event.event_id: (28800, False),
            }
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
        assert await repository.review_memory(scope, memory_ids[0], "approve") == approved
        with pytest.raises(NotFoundError):
            await repository.review_memory(scope, memory_ids[0], "reject")
        owner = PostgresRepository.from_url(_test_database_url())
        try:
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_item SET poisoning_flags=ARRAY['test_flag'] WHERE id=%s",
                    (memory_ids[1],),
                )
            with pytest.raises(NotFoundError):
                await repository.review_memory(scope, memory_ids[1], "approve")
            pending = await repository.list_memories(
                MemoryListQuery(scope=scope, statuses=["quarantined"])
            )
            assert [item.memory_id for item in pending] == [memory_ids[1]]
        finally:
            await owner.close()
        rejected = await repository.review_memory(scope, memory_ids[1], "reject")
        assert await repository.review_memory(scope, memory_ids[1], "reject") == rejected
        repeated_source = sources[1].model_copy(update={"idempotency_key": "rejected-repeat"})
        repeated_event = await repository.record_event(repeated_source)
        candidate, = extractor.extract(repeated_source)
        assert await repository.store_candidates(
            repeated_event.event_id, repeated_source, [candidate], MemoryPolicy.version,
            decisions=[CandidateDecision(candidate=candidate, outcome="stored", reason="accepted")],
        ) == []
        repeated_decision, = await repository.list_candidate_decisions(scope, 1)
        assert repeated_decision.outcome == "rejected"
        assert repeated_decision.reason == "previously_rejected"
        assert repeated_decision.stored_memory_id is None
        active = await repository.list_memories(MemoryListQuery(scope=scope))
        rejected_items = await repository.list_memories(
            MemoryListQuery(scope=scope, statuses=["rejected"])
        )
        audit = await repository.list_audit(scope, 20)
        assert [item.memory_id for item in await repository.search(
            scope, "候选 1", "task", datetime.now(UTC)
        )] == [memory_ids[0]]
        owner = PostgresRepository.from_url(_test_database_url())
        try:
            for poison_flags, sensitivity in ((["test_flag"], "low"), ([], "high")):
                async with owner.engine.begin() as connection:
                    await connection.exec_driver_sql(
                        "UPDATE memory_item SET poisoning_flags=%s, sensitivity=%s WHERE id=%s",
                        (poison_flags, sensitivity, memory_ids[0]),
                    )
                for purpose in ("task", "history"):
                    assert not await repository.search(
                        scope, "候选 1", purpose, datetime.now(UTC)
                    )
        finally:
            await owner.close()
    finally:
        await repository.close()

    assert approved.status == "active"
    assert rejected.status == "rejected"
    assert [item.memory_id for item in active] == [memory_ids[0]]
    assert [item.memory_id for item in rejected_items] == [memory_ids[1]]
    assert rejected_items[0].source_event_ids == [events[1].event_id]
    assert rejected_items[0].poisoning_flags == ["test_flag"]
    assert {entry.action for entry in audit} >= {"review_approve", "review_reject"}
    assert sum(entry.action in {"review_approve", "review_reject"} for entry in audit) == 2


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
        async with repository.engine.begin() as connection:
            original_end = (await connection.exec_driver_sql(
                "UPDATE memory_item SET valid_to=clock_timestamp() WHERE id=%s RETURNING valid_to",
                (original_id,),
            )).scalar_one()
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
    old_version = next(item for item in history.items if item.memory_id == original_id)
    assert old_version.valid_to == original_end


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("encrypted", [False, True])
async def test_user_confirmed_procedure_is_scoped_correctable_and_task_matched(
    encrypted: bool,
) -> None:
    cipher = EventCipher(base64.b64encode(os.urandom(32)).decode()) if encrypted else None
    repository = PostgresRepository.from_url(_test_app_database_url(), event_cipher=cipher)
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
            occurred_at=datetime.now(UTC) - timedelta(seconds=4 - index),
        )
        for index, (source, content) in enumerate(
            (
                (EventSource.USER, "Remember this code review procedure"),
                (EventSource.USER, "Correct the code review procedure"),
                (EventSource.ASSISTANT, "Unverified assistant procedure"),
                (EventSource.USER, "During code review we verified the evidence before the conclusion"),
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

        procedure = RememberProcedureCommand(
            action="remember_procedure",
            scope=scope,
            statement=original,
            source_event_id=events[0].event_id,
            supporting_event_ids=[events[3].event_id],
        )
        with pytest.raises(NotFoundError):
            await system.execute_command_idempotent(
                procedure.model_copy(update={"supporting_event_ids": [events[2].event_id]}),
                f"command-{uuid4()}", "assistant-support",
            )
        remembered = await system.execute_command_idempotent(
            procedure,
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
        assert set(active[0].source_event_ids) == {
            events[0].event_id, events[1].event_id, events[3].event_id,
        }
        affected, _ = await repository.prohibit_events(scope, [events[3].event_id])
        assert set(affected) == {original_id, corrected.affected_memory_ids[1]}
        assert not await repository.search(
            scope, "code review conclusion", "history", datetime.now(UTC) + timedelta(minutes=1)
        )
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
async def test_memory_pagination_continues_after_cursor_row_is_deleted() -> None:
    reader = PostgresRepository.from_url(_test_app_database_url())
    worker = PostgresRepository.from_url(_test_role_url("MEMORY_TEST_WORKER_DATABASE_URL"))
    scope = MemoryScope(tenant_id=f"tenant-{uuid4()}", user_id="user-a", agent_id="agent-a")
    try:
        event = await reader.record_event(InteractionEvent(
            **scope.model_dump(), thread_id="pagination", idempotency_key="pagination",
            source=EventSource.USER, content="记住这两条分页测试记录", occurred_at=datetime.now(UTC),
        ))
        ids = [
            await reader.remember(scope, statement, event.event_id, MemoryPolicy.version)
            for statement in ("分页记录一", "分页记录二")
        ]
        first, = await reader.list_memories(MemoryListQuery(scope=scope, limit=1))
        assert first.memory_id == max(ids)
        await reader.isolate_memories(scope, [first.memory_id])
        assert await worker.process_one_deletion()
        second, = await reader.list_memories(
            MemoryListQuery(scope=scope, cursor=first.memory_id, limit=1)
        )
        assert second.memory_id == min(ids)
    finally:
        await worker.close()
        await reader.close()


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
async def test_same_subject_candidates_are_kept_as_an_unresolved_conflict(monkeypatch) -> None:
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
        lock_subject = repository._lock_subject
        acquired = {}

        async def record_subject_lock(connection, scope, subject_key):
            await lock_subject(connection, scope, subject_key)
            acquired.setdefault(asyncio.current_task(), []).append(subject_key)

        with monkeypatch.context() as patch:
            patch.setattr(repository, "_lock_subject", record_subject_lock)
            other = candidates[0].model_copy(update={
                "subject_key": "a:lock-order", "statement": "Lock-order test",
            })
            async with asyncio.timeout(8):
                [first_id, other_id], [_, second_id] = await asyncio.gather(
                    repository.store_candidates(
                        events[0].event_id, sources[0], [candidates[0], other], MemoryPolicy.version,
                    ),
                    repository.store_candidates(
                        events[1].event_id, sources[1], [other, candidates[1]], MemoryPolicy.version,
                    ),
                )
            assert list(acquired.values()) == [["a:lock-order", "preferences:theme"]] * 2
        owner = PostgresRepository.from_url(_test_database_url())
        try:
            async with owner.engine.begin() as connection:
                await connection.exec_driver_sql("DELETE FROM memory_item WHERE id=%s", (other_id,))
        finally:
            await owner.close()
        memory_ids = [first_id, second_id]
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
        resolution = CorrectCommand(
            action="correct",
            scope=scope,
            memory_id=memory_ids[0],
            replacement=sources[1].content,
            source_event_id=correction_event.event_id,
        )
        resolution_key = f"resolve-{uuid4()}"
        receipt = await repository.execute_command_idempotent(
            resolution, resolution_key, "confirm-existing-preference", MemoryPolicy.version
        )
        replay = await repository.execute_command_idempotent(
            resolution, resolution_key, "confirm-existing-preference", MemoryPolicy.version
        )
        assert replay == receipt
        resolved_id = receipt.affected_memory_ids[1]
        with pytest.raises(NotFoundError):
            await repository.execute_command_idempotent(
                resolution.model_copy(update={
                    "scope": scope.model_copy(update={"user_id": "other-user"}),
                }),
                f"resolve-other-{uuid4()}",
                "confirm-existing-preference",
                MemoryPolicy.version,
            )
        resolved = await repository.list_memories(MemoryListQuery(scope=scope))
        profile = await repository.profile(
            scope,
            max(source.occurred_at for source in sources) + timedelta(seconds=3),
        )
        for index, (event, source) in enumerate(zip(events, sources, strict=True)):
            await repository.store_candidates(
                event.event_id, source, [candidates[index].model_copy(update={
                    "statement": f"不同日期的主题选用记录 {index}",
                    "proposed_layer": MemoryLayer.EPISODIC,
                    "expires_at": source.occurred_at + timedelta(days=1),
                })], MemoryPolicy.version,
            )
        episodes = await repository.list_memories(MemoryListQuery(
            scope=scope, layers=[MemoryLayer.EPISODIC],
        ))
        assert len(episodes) == 2
        assert all(not item.conflict_memory_ids for item in episodes)
        old_until = sources[1].occurred_at
        for index, (event, source) in enumerate(zip(events, sources, strict=True)):
            await repository.store_candidates(
                event.event_id, source, [
                    candidates[index].model_copy(update={
                        "statement": f"时段偏好记录 {index}", "subject_key": "preferences:period",
                        "expires_at": old_until if index == 0 else None,
                    }),
                    candidates[index].model_copy(update={
                        "statement": "我喜欢紧凑界面" + ("。" if index else ""),
                        "subject_key": "preferences:layout",
                    }),
                ], MemoryPolicy.version,
            )
        preferences = [
            item for item in await repository.list_memories(MemoryListQuery(scope=scope))
            if item.statement.startswith(("时段偏好记录", "我喜欢紧凑界面"))
        ]
        assert len(preferences) == 4
        assert all(not item.conflict_memory_ids for item in preferences)
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
        async with (
            asyncio.timeout(8),
            asyncio.TaskGroup() as tasks,
            repository.engine.begin() as connection,
        ):
            await repository._set_scope(connection, scope)
            await repository._lock_subject(connection, scope, candidates[1].subject_key)
            approval = tasks.create_task(repository.review_memory(scope, shadow_id, "approve"))
            while True:
                waiting = await connection.exec_driver_sql(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                    "AND NOT granted AND pg_backend_pid()=ANY(pg_blocking_pids(pid)))"
                )
                if waiting.scalar_one():
                    break
                await asyncio.sleep(0.01)
            await connection.exec_driver_sql(
                "SELECT id FROM memory_item WHERE id=%s FOR UPDATE NOWAIT", (shadow_id,)
            )
        assert approval.result().status == "active"
        after = await repository.list_memories(MemoryListQuery(scope=scope))
        before_resolution = datetime.now(UTC)
        with pytest.raises(NotFoundError):
            await repository.resolve_memory_conflict_as_coexisting(
                scope.model_copy(update={"user_id": "other-user"}), active_id, shadow_id
            )

        def authenticate():
            return Identity(**scope.model_dump(), allowed_purposes={"review"}, memory_write=True)

        @asynccontextmanager
        async def lifespan(_app):
            yield

        api = create_app(MemorySystem(repository, MemoryPolicy(), 2048), authenticate, lifespan)
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            base = "/v1/memory/items"
            for left, right in ((active_id, shadow_id), (shadow_id, active_id)):
                response = await client.put(f"{base}/{left}/conflicts/{right}/coexistence")
                assert response.status_code == 204
        await repository.store_candidates(
            events[0].event_id, sources[0], [candidates[0]], MemoryPolicy.version,
        )
        coexisting = await repository.list_memories(MemoryListQuery(scope=scope))
        assert {item.memory_id for item in coexisting} == {active_id, shadow_id}
        assert all(not item.conflict_memory_ids for item in coexisting)
        historical = await repository.search(scope, "preferences:theme", "history", before_resolution)
        assert len(historical) == 2 and all(item.conflict_memory_ids for item in historical)
        audit = await repository.list_audit(scope, 20)
        assert sum(entry.action == "conflict_coexistence" for entry in audit) == 1
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
            expired = await repository.record_event(
                event.model_copy(update={"idempotency_key": "expired-extraction"})
            )
            async with worker.engine.begin() as connection:
                await connection.exec_driver_sql(
                    "UPDATE memory_event SET content='[retention-expired]', redacted_at=now() "
                    "WHERE id=%s", (expired.event_id,),
                )
            assert await worker.claim_next_extraction() is None
            status = await repository.event_processing_status(scope, expired.event_id)
            assert (status.extraction_status, status.error_code) == ("failed", "source_unavailable")
            assert not await worker.store_candidates(
                expired.event_id, event, candidates[:1], MemoryPolicy.version,
            )
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

