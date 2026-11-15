from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import structlog
from cryptography.exceptions import InvalidTag
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from memory_system.event_cipher import EventCipher
from memory_system.models import (
    AffectObservation,
    AuditEntry,
    BackgroundConsolidationDraftBatch,
    BackgroundConsolidationDraftPage,
    CandidateDecision,
    CandidateDecisionAudit,
    CommandReceipt,
    CorrectCommand,
    CorrectProcedureCommand,
    DeletionStatus,
    DoNotRememberCommand,
    EventJob,
    EventProcessingStatus,
    EventSource,
    ForgetCommand,
    InteractionEvent,
    MemoryCandidate,
    MemoryCommand,
    MemoryContextItem,
    MemoryLayer,
    MemoryListQuery,
    MemoryReviewReceipt,
    MemoryScope,
    MemorySummary,
    RecallDiagnostic,
    RecordReceipt,
    RememberCommand,
    RememberProcedureCommand,
)
from memory_system.policy import explicitly_opts_out, is_memory_opt_out

_BACKGROUND_CONSOLIDATION_FROM = """
    FROM memory_event e
    JOIN memory_consolidation_opt_in consent USING (tenant_id, user_id, agent_id)
"""
_BACKGROUND_CONSOLIDATION_FILTER = """
    WHERE e.source='user' AND NOT e.prohibited_from_memory AND e.redacted_at IS NULL
      AND e.occurred_at <= now() AND e.background_eligible_at >= consent.enabled_at
"""
_BACKGROUND_CONSOLIDATION_EVENTS = _BACKGROUND_CONSOLIDATION_FROM + _BACKGROUND_CONSOLIDATION_FILTER


class IdempotencyConflictError(Exception):
    pass


class NotFoundError(Exception):
    pass


class MemoryDeletionInProgress(Exception):
    pass


class MemorySourcesChanged(Exception):
    pass


class DeletionCanaryFailed(RuntimeError):
    pass


class EventPayloadUnreadable(RuntimeError):
    pass


class InvalidMemoryExpiry(ValueError):
    pass


class RateLimitExceeded(Exception):
    def __init__(self, operation: str, retry_after_seconds: int = 60) -> None:
        self.operation = operation
        self.retry_after_seconds = retry_after_seconds
        super().__init__(operation)


class PostgresRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        event_write_limit_per_minute: int = 120,
        command_write_limit_per_minute: int = 30,
        event_cipher: EventCipher | None = None,
        rotate_event_ciphertext: bool = False,
    ) -> None:
        self.engine = engine
        self.event_write_limit_per_minute = event_write_limit_per_minute
        self.command_write_limit_per_minute = command_write_limit_per_minute
        self.event_cipher = event_cipher
        self.rotate_event_ciphertext = rotate_event_ciphertext

    @classmethod
    def from_url(
        cls,
        database_url: str,
        event_write_limit_per_minute: int = 120,
        command_write_limit_per_minute: int = 30,
        event_cipher: EventCipher | None = None,
        rotate_event_ciphertext: bool = False,
    ) -> PostgresRepository:
        return cls(
            create_async_engine(database_url, pool_pre_ping=True),
            event_write_limit_per_minute,
            command_write_limit_per_minute,
            event_cipher,
            rotate_event_ciphertext,
        )

    async def close(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(
                text(
                    """
                    SELECT memory_event.ciphertext, memory_event.ciphertext_key_id,
                           memory_event.ciphertext_retry_at, memory_event.background_eligible_at,
                           memory_consolidation_opt_in.enabled_at,
                           memory_consolidation_run.drafts, memory_consolidation_run.source_ids,
                           memory_item.last_reinforced_at, memory_relation.resolved_at
                    FROM memory_event, memory_item, memory_outbox, memory_evidence,
                         command_dedup, deletion_job, memory_relation, memory_audit,
                         memory_candidate_decision, memory_recall_diagnostic,
                         memory_write_rate_limit, memory_embedding, memory_dependency,
                         memory_affect_observation, memory_consolidation_opt_in,
                         memory_consolidation_run
                    LIMIT 0
                    """
                )
            )

    @asynccontextmanager
    async def _try_worker_lock(self, key: str) -> AsyncIterator[bool]:
        async with self.engine.begin() as connection:
            lock = await connection.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": key},
            )
            yield lock.scalar_one()

    def embedding_backfill_lock(self, model: str, dimensions: int):
        return self._try_worker_lock(f"persistent-memory-embedding:{dimensions}:{model}")

    def background_consolidation_lock(self):
        return self._try_worker_lock("persistent-memory-background-consolidation")

    async def pending_embeddings(self, model: str, dimensions: int) -> list[tuple[str, str]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT m.id, m.statement FROM memory_item m
                    WHERE m.status='active' AND m.layer IN ('L2','L3')
                      AND m.sensitivity IN ('low','medium')
                      AND cardinality(m.poisoning_flags)=0
                      AND (m.expires_at IS NULL OR m.expires_at>now())
                      AND NOT EXISTS (
                        SELECT 1 FROM memory_embedding e WHERE e.memory_id=m.id
                          AND e.model=:model AND e.dimensions=:dimensions
                      )
                    ORDER BY m.created_at, m.id LIMIT 16
                    """
                ),
                {"model": model, "dimensions": dimensions},
            )
            return [(str(row.id), row.statement) for row in result]

    async def store_embedding(
        self, memory_id: str, statement: str, model: str, vector: list[float]
    ) -> bool:
        async with self.engine.begin() as connection:
            stored = await connection.execute(
                text(
                    """
                    WITH eligible AS (
                        SELECT id FROM memory_item
                        WHERE id=:id AND statement=:statement AND status='active'
                          AND layer IN ('L2','L3')
                          AND sensitivity IN ('low','medium')
                          AND cardinality(poisoning_flags)=0
                          AND (expires_at IS NULL OR expires_at>now())
                        FOR UPDATE
                    )
                    INSERT INTO memory_embedding (memory_id, model, dimensions, embedding)
                    SELECT id, :model, :dimensions, CAST(:embedding AS real[])
                    FROM eligible
                    ON CONFLICT (memory_id, model, dimensions) DO UPDATE
                    SET embedding=EXCLUDED.embedding
                    RETURNING memory_id
                    """
                ),
                {
                    "id": memory_id,
                    "statement": statement,
                    "model": model,
                    "dimensions": len(vector),
                    "embedding": vector,
                },
            )
            return stored.first() is not None

    async def consume_consolidation_quota(self, scope: MemoryScope, limit: int) -> None:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            await self._consume_write_quota(connection, scope, "consolidation", limit)

    async def set_background_consolidation(self, scope: MemoryScope, enabled: bool) -> None:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            if enabled:
                statement = """
                    INSERT INTO memory_consolidation_opt_in (tenant_id, user_id, agent_id)
                    VALUES (:tenant_id, :user_id, :agent_id)
                    ON CONFLICT DO NOTHING
                """
            else:
                statement = """
                    DELETE FROM memory_consolidation_opt_in
                    WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                """
            await connection.execute(text(statement), scope.model_dump())

    async def discover_consolidation_threads(self, scope: MemoryScope) -> list[str]:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            rows = await connection.execute(
                text(
                    """
                    SELECT thread_id FROM memory_event e
                    WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                      AND source='user' AND NOT prohibited_from_memory AND redacted_at IS NULL
                      AND occurred_at <= now()
                    GROUP BY thread_id
                    HAVING count(*) >= 2
                    ORDER BY max(occurred_at) DESC, thread_id
                    LIMIT 20
                    """
                ),
                scope.model_dump(),
            )
            return list(rows.scalars())

    async def discover_background_consolidation_batches(
        self, *, embedding_model: str | None = None, embedding_dimensions: int | None = None,
    ) -> list[tuple[MemoryScope, list[str]]]:
        async with self.engine.connect() as connection:
            rows = await connection.execute(text(f"""
                WITH eligible_evidence AS (
                    SELECT DISTINCT e.tenant_id, e.user_id, e.agent_id, e.thread_id,
                           e.id, e.occurred_at, m.subject_key, m.id AS memory_id,
                           m.updated_at AS memory_updated_at
                    {_BACKGROUND_CONSOLIDATION_FROM}
                    JOIN memory_evidence ev ON ev.event_id=e.id
                    JOIN memory_item m ON m.id=ev.memory_id
                      AND (m.tenant_id, m.user_id, m.agent_id)=(e.tenant_id, e.user_id, e.agent_id)
                    {_BACKGROUND_CONSOLIDATION_FILTER}
                      AND m.status='active'
                      AND m.sensitivity IN ('low','medium') AND cardinality(m.poisoning_flags)=0
                      AND (m.valid_from IS NULL OR m.valid_from<=now())
                      AND (m.valid_to IS NULL OR m.valid_to>now())
                      AND (m.expires_at IS NULL OR m.expires_at>now())
                ), grouped_events AS (
                    SELECT e.tenant_id, e.user_id, e.agent_id, e.thread_id,
                           e.id, e.occurred_at, e.thread_id AS group_key, false AS cross_thread
                    {_BACKGROUND_CONSOLIDATION_EVENTS}
                    UNION ALL
                    SELECT DISTINCT tenant_id, user_id, agent_id, thread_id,
                           id, occurred_at, subject_key, true
                    FROM eligible_evidence WHERE subject_key IS NOT NULL
                ), event_vectors AS (
                    SELECT DISTINCT ON (evidence.id) evidence.*, v.embedding
                    FROM eligible_evidence evidence
                    JOIN memory_embedding v ON v.memory_id=evidence.memory_id
                    WHERE v.model=:embedding_model AND v.dimensions=:embedding_dimensions
                    ORDER BY evidence.id, evidence.memory_updated_at DESC, evidence.memory_id
                ), recent_vectors AS (
                    SELECT *, row_number() OVER (
                        PARTITION BY tenant_id, user_id, agent_id
                        ORDER BY occurred_at DESC, id DESC
                    ) AS position
                    FROM event_vectors
                ), bounded_vectors AS MATERIALIZED (
                    SELECT recent.*, norms.squared_norm
                    FROM recent_vectors recent
                    CROSS JOIN LATERAL (
                        SELECT sum(value::double precision * value) AS squared_norm
                        FROM unnest(recent.embedding) AS components(value)
                    ) norms
                    WHERE position<=32 AND norms.squared_norm>0
                ), ranked AS (
                    SELECT *, row_number() OVER (
                        PARTITION BY tenant_id, user_id, agent_id, cross_thread, group_key
                        ORDER BY occurred_at DESC, id DESC
                    ) AS position FROM grouped_events
                ), batches AS (
                    SELECT tenant_id, user_id, agent_id,
                           array_agg(id ORDER BY id) AS source_ids, max(occurred_at) AS latest
                    FROM ranked WHERE position<=8
                    GROUP BY tenant_id, user_id, agent_id, cross_thread, group_key
                    HAVING count(*)>=2 AND (NOT cross_thread OR count(DISTINCT thread_id)>=2)
                    UNION ALL
                    SELECT a.tenant_id, a.user_id, a.agent_id,
                           ARRAY[a.id,b.id], greatest(a.occurred_at,b.occurred_at)
                    FROM bounded_vectors a JOIN bounded_vectors b
                      USING (tenant_id, user_id, agent_id)
                    CROSS JOIN LATERAL (
                        -- Event IDs are globally unique, so the pair identifies the source batch.
                        SELECT CASE WHEN ARRAY[a.id,b.id] NOT IN (
                            SELECT r.source_ids FROM memory_consolidation_run r
                            WHERE cardinality(r.source_ids)=2 AND (r.retry_at IS NULL OR r.retry_at>now())
                        ) THEN (
                            SELECT sum(x::double precision * y)
                                / sqrt(a.squared_norm * b.squared_norm)
                            FROM unnest(a.embedding, b.embedding) AS pairs(x,y)
                        ) END AS score
                    ) similarity
                    WHERE a.id<b.id
                      AND a.thread_id<>b.thread_id AND a.memory_id<>b.memory_id
                      AND (a.subject_key IS NULL OR a.subject_key IS DISTINCT FROM b.subject_key)
                      AND similarity.score BETWEEN 0.9 AND 1.000001
                )
                SELECT b.tenant_id, b.user_id, b.agent_id, b.source_ids
                FROM batches b
                WHERE NOT EXISTS (
                    SELECT 1 FROM memory_consolidation_run r
                    WHERE (r.tenant_id, r.user_id, r.agent_id)=(b.tenant_id, b.user_id, b.agent_id)
                      AND r.source_ids=b.source_ids AND (r.retry_at IS NULL OR r.retry_at>now())
                )
                GROUP BY b.tenant_id, b.user_id, b.agent_id, b.source_ids
                ORDER BY min(b.latest), b.tenant_id, b.user_id, b.agent_id, b.source_ids
                LIMIT 20
            """), {"embedding_model": embedding_model, "embedding_dimensions": embedding_dimensions})
            return [
                (MemoryScope.model_validate(row), [str(value) for value in row["source_ids"]])
                for row in rows.mappings()
            ]

    async def discover_consolidation_sources(self, scope: MemoryScope, thread_id: str) -> list[str]:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            rows = await connection.execute(
                text(
                    """
                    SELECT id FROM memory_event
                    WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                      AND thread_id=:thread_id AND source='user'
                      AND NOT prohibited_from_memory AND redacted_at IS NULL
                      AND occurred_at <= now()
                    ORDER BY occurred_at DESC, id DESC LIMIT 8
                    """
                ),
                {**scope.model_dump(), "thread_id": thread_id},
            )
            return [str(event_id) for event_id in rows.scalars()]

    @staticmethod
    def background_source_digest(event_ids: Sequence[str]) -> str:
        canonical_ids = sorted({str(UUID(event_id)) for event_id in event_ids})
        if not 2 <= len(canonical_ids) <= 8:
            raise ValueError("consolidation requires two to eight distinct events")
        return hashlib.sha256(",".join(canonical_ids).encode()).hexdigest()

    async def list_background_consolidation_drafts(
        self, scope: MemoryScope, *, cursor: str = "", limit: int = 20,
    ) -> BackgroundConsolidationDraftPage:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            result = await connection.execute(
                text("""
                    SELECT source_digest, drafts FROM memory_consolidation_run
                    WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                      AND drafts <> '[]'::jsonb
                      AND source_digest > :cursor
                    ORDER BY source_digest LIMIT :limit
                """),
                {**scope.model_dump(), "cursor": cursor, "limit": limit + 1},
            )
            rows = result.mappings().all()
            return BackgroundConsolidationDraftPage(
                batches=[BackgroundConsolidationDraftBatch.model_validate(row) for row in rows[:limit]],
                next_cursor=rows[limit - 1]["source_digest"] if len(rows) > limit else None,
            )

    async def dismiss_background_consolidation_drafts(
        self, scope: MemoryScope, source_digest: str,
    ) -> None:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            await connection.execute(
                text("""
                    UPDATE memory_consolidation_run SET drafts='[]'::jsonb
                    WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                      AND source_digest=:digest AND drafts <> '[]'::jsonb
                """),
                {**scope.model_dump(), "digest": source_digest},
            )

    async def background_consolidation_completed(
        self, scope: MemoryScope, event_ids: Sequence[str]
    ) -> bool:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text("""
                    SELECT EXISTS (
                        SELECT 1 FROM memory_consolidation_run
                        WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                          AND source_digest=:digest AND retry_at IS NULL
                    )
                """),
                {**scope.model_dump(), "digest": self.background_source_digest(event_ids)},
            )
            return result.scalar_one()

    async def complete_background_consolidation(
        self, scope: MemoryScope, event_ids: Sequence[str],
        *, drafts: Sequence[MemoryCandidate] = (), retry: bool = False,
    ) -> bool:
        digest = self.background_source_digest(event_ids)
        ids = sorted({UUID(event_id) for event_id in event_ids})
        if len(drafts) > 3 or any(
            not 2 <= len(set(draft.source_event_ids)) <= len(ids)
            or not set(draft.source_event_ids).issubset(str(event_id) for event_id in ids)
            or draft.proposed_layer not in (MemoryLayer.EPISODIC, MemoryLayer.PROCEDURAL)
            for draft in drafts
        ):
            raise ValueError("invalid background consolidation drafts")
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(f"""
                    INSERT INTO memory_consolidation_run
                        (tenant_id, user_id, agent_id, source_digest, drafts, source_ids, retry_at)
                    SELECT :tenant_id, :user_id, :agent_id, :digest, CAST(:drafts AS jsonb),
                           CAST(:ids AS uuid[]),
                           CASE WHEN :retry THEN now() + interval '10 minutes' END
                    WHERE :source_count = (
                        SELECT count(*) FROM (
                            SELECT e.id
                            {_BACKGROUND_CONSOLIDATION_EVENTS}
                              AND e.tenant_id=:tenant_id AND e.user_id=:user_id
                              AND e.agent_id=:agent_id AND e.id=ANY(CAST(:ids AS uuid[]))
                            ORDER BY e.id FOR UPDATE OF e FOR KEY SHARE OF consent
                        ) eligible
                    )
                    ON CONFLICT (tenant_id, user_id, agent_id, source_digest) DO UPDATE
                    SET drafts=EXCLUDED.drafts, retry_at=EXCLUDED.retry_at
                    WHERE memory_consolidation_run.retry_at IS NOT NULL
                    RETURNING source_digest
                """),
                {
                    **scope.model_dump(), "digest": digest, "ids": ids, "source_count": len(ids),
                    "drafts": json.dumps([draft.model_dump(mode="json") for draft in drafts]),
                    "retry": retry,
                },
            )
            return result.scalar_one_or_none() is not None

    async def consolidation_events(
        self, scope: MemoryScope, event_ids: list[str], *, background: bool = False
    ) -> list[tuple[str, InteractionEvent]]:
        ids = sorted(set(event_ids))
        if not 2 <= len(ids) <= 8:
            raise ValueError("consolidation requires two to eight distinct events")
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            rows = await connection.execute(
                text(
                    """
                    SELECT id, tenant_id, user_id, agent_id, thread_id, idempotency_key,
                           source, content, occurred_at, occurred_utc_offset_seconds, metadata, ciphertext
                    FROM memory_event
                    WHERE id=ANY(CAST(:ids AS uuid[]))
                      AND tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                      AND source='user' AND prohibited_from_memory=false AND redacted_at IS NULL
                      AND occurred_at <= now()
                      AND (NOT :background OR EXISTS (
                          SELECT 1 FROM memory_consolidation_opt_in consent
                          WHERE consent.tenant_id=memory_event.tenant_id
                            AND consent.user_id=memory_event.user_id
                            AND consent.agent_id=memory_event.agent_id
                            AND consent.enabled_at <= memory_event.background_eligible_at
                      ))
                    ORDER BY occurred_at, id
                    """
                ),
                {"ids": ids, "background": background, **scope.model_dump()},
            )
            events = [
                (str(row["id"]), InteractionEvent.model_validate(
                    self._event_payload(str(row["id"]), row)
                ))
                for row in rows.mappings()
            ]
        if len(events) != len(ids):
            raise NotFoundError("consolidation sources")
        return events

    async def record_event(self, event: InteractionEvent) -> RecordReceipt:
        canonical_payload = json.dumps(
            {
                "thread_id": event.thread_id,
                "source": event.source.value,
                "content": event.content,
                "occurred_at": event.occurred_at.isoformat(),
                "metadata": event.metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
        event_id = str(uuid4())
        opted_out = explicitly_opts_out(event)
        extraction_allowed = (
            event.source is EventSource.USER and event.allow_memory_extraction and not opted_out
        )
        params = {
            **event.model_dump(mode="json"),
            "id": event_id,
            "payload_hash": payload_hash,
            "opted_out": opted_out,
            "extraction_allowed": extraction_allowed,
            "utc_offset": int(event.occurred_at.utcoffset().total_seconds()),
            "metadata": json.dumps(event.metadata, ensure_ascii=False),
            "ciphertext": None,
            "ciphertext_key_id": None,
        }
        if self.event_cipher is not None:
            params.update(
                content="",
                metadata="{}",
                ciphertext=self.event_cipher.encrypt(event_id, event.content, event.metadata),
                ciphertext_key_id=self.event_cipher.key_id,
            )
        async with self.engine.begin() as connection:
            await self._set_scope(connection, event_scope(event))
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO memory_event (
                        id, tenant_id, user_id, agent_id, thread_id, idempotency_key,
                        payload_hash, source, content, occurred_at, occurred_utc_offset_seconds, metadata,
                        ciphertext, ciphertext_key_id, prohibited_from_memory, background_eligible_at
                    ) VALUES (
                        :id, :tenant_id, :user_id, :agent_id, :thread_id, :idempotency_key,
                        :payload_hash, :source, :content, :occurred_at, :utc_offset, CAST(:metadata AS jsonb),
                        :ciphertext, :ciphertext_key_id, :opted_out,
                        CASE WHEN :extraction_allowed THEN clock_timestamp() END
                    )
                    ON CONFLICT (tenant_id, user_id, agent_id, idempotency_key) DO NOTHING
                    RETURNING id
                    """
                ),
                params,
            )
            row = inserted.mappings().first()
            if row:
                await self._consume_write_quota(
                    connection,
                    event_scope(event),
                    "event",
                    self.event_write_limit_per_minute,
                )
                extraction_status = "not_applicable"
                if extraction_allowed:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO memory_outbox (
                                event_id, tenant_id, user_id, agent_id, kind, status
                            ) VALUES (
                                :event_id, :tenant_id, :user_id, :agent_id, 'extract', 'pending'
                            )
                            """
                        ),
                        {"event_id": row["id"], **event_scope(event).model_dump()},
                    )
                    extraction_status = "pending"
                elif event.source is EventSource.USER:
                    extraction_status = "disabled"
                return RecordReceipt(
                    event_id=str(row["id"]),
                    recorded=True,
                    duplicate=False,
                    extraction_status=extraction_status,
                )

            existing = await connection.execute(
                text(
                    """
                    SELECT id, payload_hash FROM memory_event
                    WHERE tenant_id=:tenant_id AND user_id=:user_id
                      AND agent_id=:agent_id AND idempotency_key=:idempotency_key
                    """
                ),
                params,
            )
            prior = existing.mappings().one()
            if prior["payload_hash"] != payload_hash:
                raise IdempotencyConflictError(event.idempotency_key)
            job = await connection.execute(
                text(
                    """
                    SELECT status FROM memory_outbox
                    WHERE event_id=:event_id ORDER BY id DESC LIMIT 1
                    """
                ),
                {"event_id": prior["id"]},
            )
            return RecordReceipt(
                event_id=str(prior["id"]),
                recorded=False,
                duplicate=True,
                extraction_status=job.scalar_one_or_none() or "disabled_or_not_applicable",
            )

    async def event_processing_status(
        self, scope: MemoryScope, event_id: str
    ) -> EventProcessingStatus:
        async with self.engine.connect() as connection:
            await self._set_scope(connection, scope)
            result = await connection.execute(
                text(
                    """
                    SELECT e.id,
                           COALESCE(o.status, 'disabled_or_not_applicable') AS status,
                           COALESCE(o.attempts, 0) AS attempts,
                           o.error_code,
                           COALESCE(o.completed_at, o.locked_at, o.created_at, e.recorded_at)
                             AS updated_at
                    FROM memory_event e
                    LEFT JOIN LATERAL (
                        SELECT status, attempts, error_code, completed_at, locked_at, created_at
                        FROM memory_outbox WHERE event_id=e.id ORDER BY id DESC LIMIT 1
                    ) o ON true
                    WHERE e.id=:event_id AND e.tenant_id=:tenant_id
                      AND e.user_id=:user_id AND e.agent_id=:agent_id
                    """
                ),
                {"event_id": event_id, **scope.model_dump()},
            )
            row = result.mappings().first()
            if not row:
                raise NotFoundError(event_id)
            return EventProcessingStatus(
                event_id=str(row["id"]),
                extraction_status=row["status"],
                attempts=row["attempts"],
                error_code=row["error_code"],
                updated_at=row["updated_at"],
            )

    async def claim_next_extraction(self) -> EventJob | None:
        async with self.engine.begin() as connection:
            claimed = await connection.execute(
                text(
                    """
                    WITH picked AS (
                        SELECT id FROM memory_outbox
                        WHERE kind='extract' AND attempts < 5
                          AND (
                            (status IN ('pending','retry') AND available_at <= now())
                            OR (status='processing' AND locked_at < now() - interval '5 minutes')
                          )
                        ORDER BY id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE memory_outbox o
                    SET status='processing', locked_at=now(), attempts=o.attempts + 1
                    FROM picked
                    WHERE o.id=picked.id
                    RETURNING o.id, o.event_id, o.attempts
                    """
                )
            )
            job = claimed.mappings().first()
            if not job:
                return None
            result = await connection.execute(
                text(
                    """
                    SELECT tenant_id, user_id, agent_id, thread_id, idempotency_key,
                           source, content, occurred_at, occurred_utc_offset_seconds, metadata, ciphertext,
                           prohibited_from_memory, redacted_at
                    FROM memory_event WHERE id=:event_id
                    """
                ),
                {"event_id": job["event_id"]},
            )
            event = result.mappings().one()
        if event["prohibited_from_memory"] or event["redacted_at"] is not None:
            await self.fail_extraction(
                job["id"], job["attempts"], "source_unavailable", terminal=True
            )
            return None
        event_id = str(job["event_id"])
        try:
            payload = InteractionEvent.model_validate(self._event_payload(event_id, event))
        except (EventPayloadUnreadable, ValueError):
            await self.fail_extraction(job["id"], job["attempts"], "EventPayloadUnreadable")
            raise EventPayloadUnreadable("could not decode extraction event") from None
        return EventJob(
            job_id=job["id"], event_id=event_id, attempts=job["attempts"], event=payload
        )

    def _event_payload(self, event_id: str, row: Any) -> dict[str, Any]:
        payload = dict(row)
        utc_offset = payload.pop("occurred_utc_offset_seconds", None)
        if utc_offset is not None:
            payload["occurred_at"] = payload["occurred_at"].astimezone(
                timezone(timedelta(seconds=utc_offset))
            )
        ciphertext = payload.pop("ciphertext")
        if ciphertext is not None:
            if self.event_cipher is None:
                raise EventPayloadUnreadable("event encryption key is unavailable")
            try:
                payload.update(self.event_cipher.decrypt(event_id, ciphertext))
            except (InvalidTag, ValueError):
                raise EventPayloadUnreadable("could not decode event payload") from None
        return payload

    async def store_candidates(
        self,
        event_id: str,
        event: InteractionEvent,
        candidates: list[MemoryCandidate],
        policy_version: str,
        status: str = "active",
        decisions: list[CandidateDecision] | None = None,
        *,
        job: EventJob | None = None,
    ) -> list[str]:
        scope = event_scope(event)
        stored: list[str] = []
        stored_by_fingerprint: dict[tuple[MemoryLayer, str], str] = {}
        rejected_fingerprints: set[tuple[MemoryLayer, str]] = set()
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            source = await connection.execute(
                text(
                    """
                    SELECT prohibited_from_memory, redacted_at, occurred_at FROM memory_event
                    WHERE id=:event_id AND tenant_id=:tenant_id AND user_id=:user_id
                      AND agent_id=:agent_id
                    FOR UPDATE
                    """
                ),
                {"event_id": event_id, **scope.model_dump()},
            )
            source_row = source.mappings().first()
            if (
                source_row is None or source_row["prohibited_from_memory"]
                or source_row["redacted_at"] is not None
            ):
                return []
            if job is not None:
                lease = await connection.execute(
                    text(
                        "SELECT id FROM memory_outbox WHERE id=:id AND event_id=:event_id "
                        "AND status='processing' AND attempts=:attempts FOR UPDATE"
                    ),
                    {"id": job.job_id, "event_id": event_id, "attempts": job.attempts},
                )
                if lease.first() is None:
                    return []
            conflict_subjects = {
                candidate.subject_key
                for candidate in candidates
                if candidate.subject_key and candidate.valid_to is None
            } if status == "active" and event.source is EventSource.USER else set()
            # Model output order varies across workers; acquire subject locks before writing rows.
            for subject_key in sorted(conflict_subjects):
                await self._lock_subject(connection, scope, subject_key)
            for candidate in candidates:
                conflict_eligible = (
                    candidate.subject_key in conflict_subjects and candidate.valid_to is None
                )
                fingerprint = hashlib.sha256(
                    f"{candidate.kind}\0{' '.join(candidate.statement.lower().split())}".encode()
                ).hexdigest()
                memory_id = str(uuid4())
                observation = candidate.affect
                if (
                    candidate.proposed_layer is not MemoryLayer.WORKING
                    or (observation and observation.observed_at != source_row["occurred_at"])
                ):
                    observation = None
                affect = observation.model_dump_json() if observation is not None else None
                row = await connection.execute(
                    text(
                        """
                        INSERT INTO memory_item (
                            id, tenant_id, user_id, agent_id, statement, layer, kind,
                            status, confidence, policy_version, valid_from, valid_to,
                            expires_at, fingerprint, future_utility, durability, affect,
                            subject_key, sensitivity, poisoning_flags
                        ) VALUES (
                            :id, :tenant_id, :user_id, :agent_id, :statement, :layer, :kind,
                            :status, :confidence, :policy_version, :valid_from, :valid_to,
                            :expires_at, :fingerprint, :future_utility, :durability,
                            CAST(:affect AS jsonb), :subject_key, :sensitivity,
                            CAST(:poisoning_flags AS text[])
                        )
                        ON CONFLICT (tenant_id, user_id, agent_id, layer, fingerprint)
                        WHERE fingerprint IS NOT NULL
                        DO UPDATE SET
                            confidence=GREATEST(memory_item.confidence, EXCLUDED.confidence),
                            expires_at=GREATEST(memory_item.expires_at, EXCLUDED.expires_at),
                            status=CASE
                                WHEN memory_item.status='quarantined'
                                  AND EXCLUDED.status='active' THEN 'active'
                                WHEN memory_item.layer='L1' AND memory_item.status='expired'
                                  AND EXCLUDED.status='active'
                                  AND EXCLUDED.expires_at > now() THEN 'active'
                                ELSE memory_item.status
                            END,
                            subject_key=COALESCE(memory_item.subject_key, EXCLUDED.subject_key),
                            affect=CASE
                                WHEN memory_item.layer='L1' AND EXCLUDED.affect IS NOT NULL
                                  AND (
                                    memory_item.affect IS NULL
                                    OR (EXCLUDED.affect->>'observed_at')::timestamptz
                                       >= (memory_item.affect->>'observed_at')::timestamptz
                                  ) THEN EXCLUDED.affect
                                ELSE memory_item.affect
                            END,
                            sensitivity=EXCLUDED.sensitivity,
                            poisoning_flags=EXCLUDED.poisoning_flags,
                            updated_at=now()
                        WHERE memory_item.status NOT IN ('deleting','rejected')
                        RETURNING id, status
                        """
                    ),
                    {
                        "id": memory_id,
                        "statement": candidate.statement,
                        "layer": candidate.proposed_layer.value,
                        "kind": candidate.kind,
                        "confidence": candidate.confidence,
                        "policy_version": policy_version,
                        "status": status,
                        "valid_from": candidate.valid_from,
                        "valid_to": candidate.valid_to,
                        "expires_at": candidate.expires_at,
                        "fingerprint": fingerprint,
                        "future_utility": candidate.future_utility,
                        "durability": candidate.durability,
                        "affect": affect,
                        "subject_key": candidate.subject_key,
                        "sensitivity": candidate.sensitivity,
                        "poisoning_flags": candidate.poisoning_flags,
                        **scope.model_dump(),
                    },
                )
                item = row.mappings().one_or_none()
                key = (candidate.proposed_layer, fingerprint)
                if item is None:
                    existing = await connection.execute(
                        text(
                            """
                            SELECT status FROM memory_item
                            WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                              AND layer=:layer AND fingerprint=:fingerprint
                            """
                        ),
                        {**scope.model_dump(), "layer": candidate.proposed_layer.value,
                         "fingerprint": fingerprint},
                    )
                    if existing.scalar_one_or_none() == "rejected":
                        rejected_fingerprints.add(key)
                        continue
                    raise MemoryDeletionInProgress()
                stored_id = str(item["id"])
                stored_by_fingerprint[key] = stored_id
                await connection.execute(
                    text(
                        """
                        INSERT INTO memory_evidence (memory_id, event_id)
                        VALUES (:memory_id, :event_id)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {"memory_id": stored_id, "event_id": event_id},
                )
                await self._audit(connection, scope, "automatic_memory", stored_id)
                if observation is not None:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO memory_affect_observation (
                                memory_id, event_id, affect, observed_at, expires_at
                            ) VALUES (:memory_id, :event_id, CAST(:affect AS jsonb),
                                      :observed_at, :expires_at)
                            ON CONFLICT DO NOTHING
                            """
                        ),
                        {
                            "memory_id": stored_id, "event_id": event_id,
                            "affect": affect,
                            "observed_at": observation.observed_at,
                            "expires_at": candidate.expires_at,
                        },
                    )
                if conflict_eligible and item["status"] == "active" and await self._link_unresolved_conflicts(
                    connection,
                    scope,
                    stored_id,
                    candidate.subject_key or "",
                    candidate.kind,
                    candidate.statement,
                    candidate.confidence,
                ):
                    await self._audit(connection, scope, "conflict_detected", stored_id)
                stored.append(stored_id)
            for ordinal, candidate_decision in enumerate(decisions or []):
                candidate = candidate_decision.candidate
                fingerprint = hashlib.sha256(
                    f"{candidate.kind}\0{' '.join(candidate.statement.lower().split())}".encode()
                ).hexdigest()
                key = (candidate.proposed_layer, fingerprint)
                outcome = candidate_decision.outcome
                reason = candidate_decision.reason
                if key in rejected_fingerprints and outcome != "rejected":
                    outcome, reason = "rejected", "previously_rejected"
                await connection.execute(
                    text(
                        """
                        INSERT INTO memory_candidate_decision (
                            id, tenant_id, user_id, agent_id, event_id,
                            candidate_ordinal, kind, proposed_layer, subject_key,
                            sensitivity, poisoning_flags, outcome, reason,
                            policy_version, stored_memory_id
                        ) VALUES (
                            :id, :tenant_id, :user_id, :agent_id, :event_id,
                            :candidate_ordinal, :kind, :proposed_layer, :subject_key,
                            :sensitivity, CAST(:poisoning_flags AS text[]), :outcome,
                            :reason, :policy_version, CAST(:stored_memory_id AS uuid)
                        )
                        ON CONFLICT (event_id, candidate_ordinal) DO NOTHING
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "event_id": event_id,
                        "candidate_ordinal": ordinal,
                        "kind": "redacted" if outcome == "rejected" else candidate.kind,
                        "proposed_layer": candidate.proposed_layer.value,
                        "subject_key": (
                            None if outcome == "rejected" else candidate.subject_key
                        ),
                        "sensitivity": candidate.sensitivity,
                        "poisoning_flags": (
                            [] if outcome == "rejected" else candidate.poisoning_flags
                        ),
                        "outcome": outcome,
                        "reason": reason,
                        "policy_version": policy_version,
                        "stored_memory_id": (
                            None if outcome == "rejected" else stored_by_fingerprint.get(key)
                        ),
                        **scope.model_dump(),
                    },
                )
        return stored

    async def complete_extraction(self, job_id: int, attempts: int) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE memory_outbox
                    SET status='completed', completed_at=now(), locked_at=NULL, error_code=NULL
                    WHERE id=:id AND status='processing' AND attempts=:attempts
                    """
                ),
                {"id": job_id, "attempts": attempts},
            )

    async def fail_extraction(
        self, job_id: int, attempts: int, error_code: str, *, terminal: bool = False
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE memory_outbox
                    SET status=CASE WHEN :terminal OR attempts >= 5 THEN 'failed' ELSE 'retry' END,
                        available_at=now() + make_interval(secs => LEAST(300, attempts * attempts)),
                        locked_at=NULL,
                        completed_at=CASE WHEN :terminal OR attempts >= 5 THEN now() ELSE NULL END,
                        error_code=:error_code
                    WHERE id=:id AND status='processing' AND attempts=:attempts
                    """
                ),
                {"id": job_id, "attempts": attempts,
                 "error_code": error_code[:120], "terminal": terminal},
            )

    async def execute_command_idempotent(
        self,
        command: MemoryCommand,
        key: str,
        payload_hash: str,
        policy_version: str,
        *,
        background_draft: tuple[str, int, MemoryCandidate] | None = None,
    ) -> CommandReceipt:
        scope = command.scope
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO command_dedup (
                        tenant_id, user_id, agent_id, idempotency_key, payload_hash, status
                    ) VALUES (
                        :tenant_id, :user_id, :agent_id, :key, :payload_hash, 'processing'
                    )
                    ON CONFLICT (tenant_id, user_id, agent_id, idempotency_key) DO NOTHING
                    RETURNING idempotency_key
                    """
                ),
                {**scope.model_dump(), "key": key, "payload_hash": payload_hash},
            )
            if not inserted.first():
                prior = await connection.execute(
                    text(
                        """
                        SELECT payload_hash, receipt FROM command_dedup
                        WHERE tenant_id=:tenant_id AND user_id=:user_id
                          AND agent_id=:agent_id AND idempotency_key=:key
                        """
                    ),
                    {**scope.model_dump(), "key": key},
                )
                row = prior.mappings().one()
                if row["payload_hash"] != payload_hash:
                    raise IdempotencyConflictError(key)
                return CommandReceipt.model_validate(row["receipt"])

            # Keep privacy controls available after the memory creation quota is exhausted.
            if not isinstance(command, ForgetCommand | DoNotRememberCommand):
                await self._consume_write_quota(
                    connection,
                    scope,
                    "command",
                    self.command_write_limit_per_minute,
                )
            command_id = str(uuid4())
            if isinstance(command, RememberCommand | RememberProcedureCommand):
                supporting_ids = sorted({str(UUID(value)) for value in command.supporting_event_ids})
                if supporting_ids:
                    confirmation_id = str(UUID(command.source_event_id))
                    if confirmation_id in supporting_ids:
                        raise NotFoundError(command.source_event_id)
                    all_ids = sorted([*supporting_ids, confirmation_id])
                    evidence = await connection.execute(
                        text(
                            """
                            SELECT id, source, content, ciphertext FROM memory_event
                            WHERE id=ANY(CAST(:ids AS uuid[]))
                              AND tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                              AND source='user' AND prohibited_from_memory=false
                              AND redacted_at IS NULL
                              AND occurred_at <= now()
                            ORDER BY id FOR UPDATE
                            """
                        ),
                        {"ids": all_ids, **scope.model_dump()},
                    )
                    rows = list(evidence.mappings())
                    if len(rows) != len(all_ids) or any(
                        is_memory_opt_out(
                            row["source"], self._event_payload(str(row["id"]), row)["content"]
                        )
                        for row in rows
                    ):
                        raise NotFoundError(command.source_event_id)
                if background_draft is not None:
                    digest, ordinal, draft = background_draft
                    dismissed = await connection.execute(
                        text("""
                            UPDATE memory_consolidation_run SET drafts='[]'::jsonb
                            WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                              AND source_digest=:digest
                              AND drafts -> CAST(:ordinal AS integer) = CAST(:draft AS jsonb)
                            RETURNING source_digest
                        """),
                        {
                            **scope.model_dump(), "digest": digest, "ordinal": ordinal,
                            "draft": draft.model_dump_json(),
                        },
                    )
                    if dismissed.scalar_one_or_none() is None:
                        raise NotFoundError(digest)
                if isinstance(command, RememberProcedureCommand):
                    memory_id = await self._remember_explicit(
                        connection, scope, command.statement, command.source_event_id, policy_version,
                        layer="L4", kind="user_confirmed_procedure", audit_action="remember_procedure",
                        require_user_source=True,
                    )
                elif command.expires_at is None:
                    memory_id = await self._remember(
                        connection, scope, command.statement, command.source_event_id, policy_version
                    )
                else:
                    valid = await connection.execute(
                        text(
                            "SELECT CAST(:expiry AS timestamptz) > now() "
                            "AND CAST(:expiry AS timestamptz) <= now() + interval '180 days'"
                        ),
                        {"expiry": command.expires_at},
                    )
                    if not valid.scalar_one():
                        raise InvalidMemoryExpiry()
                    memory_id = await self._remember_explicit(
                        connection, scope, command.statement, command.source_event_id, policy_version,
                        layer="L2", kind="explicit_episode", audit_action="remember",
                        require_user_source=True, expires_at=command.expires_at,
                    )
                for event_id in supporting_ids:
                    await self._add_evidence(connection, memory_id, event_id)
                if supporting_ids:
                    await connection.execute(
                        text(
                            "INSERT INTO memory_dependency (memory_id, event_id) "
                            "VALUES (:memory_id, :event_id) ON CONFLICT DO NOTHING"
                        ),
                        [{"memory_id": memory_id, "event_id": event_id} for event_id in all_ids],
                    )
                receipt = CommandReceipt(
                    command_id=command_id,
                    status="completed",
                    affected_memory_ids=[memory_id],
                )
            elif isinstance(command, CorrectCommand):
                memory_id = await self._correct(
                    connection,
                    scope,
                    command.memory_id,
                    command.replacement,
                    command.source_event_id,
                    policy_version,
                    procedural=False,
                )
                receipt = CommandReceipt(
                    command_id=command_id,
                    status="completed",
                    affected_memory_ids=[command.memory_id, memory_id],
                )
            elif isinstance(command, CorrectProcedureCommand):
                memory_id = await self._correct(
                    connection,
                    scope,
                    command.memory_id,
                    command.replacement,
                    command.source_event_id,
                    policy_version,
                    procedural=True,
                )
                receipt = CommandReceipt(
                    command_id=command_id,
                    status="completed",
                    affected_memory_ids=[command.memory_id, memory_id],
                )
            elif isinstance(command, ForgetCommand):
                affected, job_id = await self._isolate_memories(
                    connection, scope, command.memory_ids
                )
                receipt = CommandReceipt(
                    command_id=command_id,
                    status="deletion_pending" if job_id else "completed",
                    affected_memory_ids=affected,
                    deletion_job_id=job_id,
                )
            elif isinstance(command, DoNotRememberCommand):
                affected, job_id = await self._prohibit_events(
                    connection, scope, command.event_ids
                )
                receipt = CommandReceipt(
                    command_id=command_id,
                    status="deletion_pending" if job_id else "completed",
                    affected_memory_ids=affected,
                    deletion_job_id=job_id,
                )
            else:
                raise TypeError(f"Unsupported command: {type(command).__name__}")

            await connection.execute(
                text(
                    """
                    UPDATE command_dedup
                    SET status='completed', receipt=CAST(:receipt AS jsonb), completed_at=now()
                    WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                      AND idempotency_key=:key
                    """
                ),
                {
                    **scope.model_dump(),
                    "key": key,
                    "receipt": receipt.model_dump_json(),
                },
            )
            return receipt

    async def remember(
        self,
        scope: MemoryScope,
        statement: str,
        source_event_id: str,
        policy_version: str,
    ) -> str:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            return await self._remember(
                connection, scope, statement, source_event_id, policy_version
            )

    async def _remember(
        self,
        connection: AsyncConnection,
        scope: MemoryScope,
        statement: str,
        source_event_id: str,
        policy_version: str,
    ) -> str:
        return await self._remember_explicit(
            connection,
            scope,
            statement,
            source_event_id,
            policy_version,
            layer="L3",
            kind="explicit",
            audit_action="remember",
            require_user_source=False,
        )

    async def _remember_explicit(
        self,
        connection: AsyncConnection,
        scope: MemoryScope,
        statement: str,
        source_event_id: str,
        policy_version: str,
        *,
        layer: str,
        kind: str,
        audit_action: str,
        require_user_source: bool,
        expires_at: datetime | None = None,
    ) -> str:
        memory_id = str(uuid4())
        identity = f"{kind}\0{' '.join(statement.lower().split())}"
        if expires_at is not None:
            identity += f"\0{expires_at.astimezone(UTC).isoformat()}"
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        event = await connection.execute(
            text(
                """
                SELECT source, content, ciphertext FROM memory_event
                WHERE id=:event_id AND tenant_id=:tenant_id AND user_id=:user_id
                  AND agent_id=:agent_id AND prohibited_from_memory=false
                  AND redacted_at IS NULL
                  AND (:require_user_source=false OR source='user')
                FOR UPDATE
                """
            ),
            {
                "event_id": source_event_id,
                "require_user_source": require_user_source,
                **scope.model_dump(),
            },
        )
        row = event.mappings().first()
        evidence = self._event_payload(source_event_id, row) if row is not None else None
        if evidence is None or is_memory_opt_out(evidence["source"], evidence["content"]):
            raise NotFoundError(source_event_id)
        result = await connection.execute(
            text(
                """
                INSERT INTO memory_item (
                    id, tenant_id, user_id, agent_id, statement, layer, kind,
                    status, confidence, policy_version, valid_from, fingerprint,
                    future_utility, durability, expires_at
                ) VALUES (
                    :id, :tenant_id, :user_id, :agent_id, :statement, :layer,
                    :kind, 'active', 1.0, :policy_version, now(), :fingerprint,
                    'high', CASE WHEN :layer='L2' THEN 'uncertain' ELSE 'stable' END, :expires_at
                )
                ON CONFLICT (tenant_id, user_id, agent_id, layer, fingerprint)
                WHERE fingerprint IS NOT NULL
                DO UPDATE SET
                    status=CASE
                        WHEN memory_item.status IN ('quarantined','rejected') THEN 'active'
                        ELSE memory_item.status
                    END,
                    confidence=CASE
                        WHEN memory_item.status IN ('quarantined','rejected') THEN 1.0
                        ELSE memory_item.confidence
                    END,
                    updated_at=now()
                WHERE memory_item.status <> 'deleting'
                RETURNING id
                """
            ),
            {
                "id": memory_id,
                "statement": statement,
                "layer": layer,
                "kind": kind,
                "policy_version": policy_version,
                "fingerprint": fingerprint,
                "expires_at": expires_at,
                **scope.model_dump(),
            },
        )
        stored = result.scalar_one_or_none()
        if stored is None:
            raise MemoryDeletionInProgress()
        stored_id = str(stored)
        await self._add_evidence(connection, stored_id, source_event_id)
        await self._audit(connection, scope, audit_action, stored_id)
        return stored_id

    async def correct(
        self,
        scope: MemoryScope,
        memory_id: str,
        replacement: str,
        source_event_id: str,
        policy_version: str,
    ) -> str:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            return await self._correct(
                connection,
                scope,
                memory_id,
                replacement,
                source_event_id,
                policy_version,
                procedural=False,
            )

    async def _correct(
        self,
        connection: AsyncConnection,
        scope: MemoryScope,
        memory_id: str,
        replacement: str,
        source_event_id: str,
        policy_version: str,
        *,
        procedural: bool,
    ) -> str:
        replacement_id = str(uuid4())
        params = {
            "memory_id": memory_id,
            "event_id": source_event_id,
            "procedural": procedural,
            **scope.model_dump(),
        }
        event = await connection.execute(
            text(
                """
                SELECT id, source, content, ciphertext, prohibited_from_memory, redacted_at,
                       id=:event_id AS is_correction
                FROM memory_event
                WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                  AND (id=:event_id OR id IN (
                    SELECT event_id FROM memory_dependency WHERE memory_id=:memory_id
                  ))
                ORDER BY id
                FOR UPDATE
                """
            ),
            params,
        )
        rows = list(event.mappings())
        row = next((candidate for candidate in rows if candidate["is_correction"]), None)
        if (
            row is None
            or row["redacted_at"] is not None
            or (procedural and row["source"] != "user")
            or any(candidate["prohibited_from_memory"] for candidate in rows)
        ):
            raise NotFoundError(memory_id)
        evidence = self._event_payload(source_event_id, row)
        if is_memory_opt_out(evidence["source"], evidence["content"]):
            raise NotFoundError(memory_id)
        subject_key = await self._lock_memory_subject(connection, scope, memory_id)
        owned = await connection.execute(
            text(
                """
                SELECT id, subject_key, kind, layer, expires_at FROM memory_item
                WHERE id=:memory_id AND tenant_id=:tenant_id AND user_id=:user_id
                  AND agent_id=:agent_id AND status='active'
                  AND ((:procedural=true AND layer='L4')
                    OR (:procedural=false AND layer<>'L4'))
                FOR UPDATE
                """
            ),
            params,
        )
        owned_row = owned.mappings().first()
        if owned_row is None:
            raise NotFoundError(memory_id)
        if owned_row["subject_key"] != subject_key:
            raise MemorySourcesChanged()
        dependencies = await connection.execute(
            text("SELECT event_id FROM memory_dependency WHERE memory_id=:memory_id"),
            params,
        )
        source_ids = set(dependencies.scalars())
        # A concurrent confirmation can add sources before we acquire the memory lock.
        # Retry with a fresh snapshot rather than publishing with unlocked sources.
        if not source_ids.issubset(candidate["id"] for candidate in rows):
            raise MemorySourcesChanged()
        is_summary = bool(source_ids)
        layer = owned_row["layer"]
        correction_kind = (
            "user_confirmed_procedure_correction"
            if procedural
            else "explicit_correction"
        )
        audit_action = "correct_procedure" if procedural else "correct"
        await connection.execute(
            text(
                "UPDATE memory_item SET status='superseded', valid_to=LEAST(valid_to, now()) "
                "WHERE id=:memory_id"
            ),
            params,
        )
        if subject_key and not is_summary:
            matching = await connection.execute(
                text(
                    """
                    SELECT id FROM memory_item
                    WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                      AND id<>CAST(:memory_id AS uuid) AND status='active'
                      AND layer=:layer
                      AND subject_key=:subject_key
                      AND lower(regexp_replace(trim(statement), '\\s+', ' ', 'g'))=:replacement
                    ORDER BY updated_at DESC LIMIT 1
                    FOR UPDATE
                    """
                ),
                {
                    **params,
                    "subject_key": subject_key,
                    "layer": layer,
                    "replacement": " ".join(replacement.lower().split()),
                },
            )
            matching_id = matching.scalar_one_or_none()
            if matching_id is not None:
                resolved_id = str(matching_id)
                await connection.execute(
                    text(
                        """
                        UPDATE memory_item
                        SET kind=:kind, confidence=1.0,
                            policy_version=:policy_version,
                            future_utility='high', durability='stable', updated_at=now()
                        WHERE id=:resolved_id
                        """
                    ),
                    {
                        "resolved_id": resolved_id,
                        "kind": correction_kind,
                        "policy_version": policy_version,
                    },
                )
                await self._add_evidence(connection, resolved_id, source_event_id)
                await self._audit(connection, scope, audit_action, resolved_id)
                return resolved_id
        await connection.execute(
            text(
                """
                INSERT INTO memory_item (
                    id, tenant_id, user_id, agent_id, statement, layer, kind,
                    status, confidence, policy_version, valid_from, supersedes_id,
                    subject_key, future_utility, durability, expires_at
                ) VALUES (
                    :id, :tenant_id, :user_id, :agent_id, :statement, :layer,
                    :kind, 'active', 1.0, :policy_version, now(),
                    :memory_id, :subject_key, 'high',
                    CASE WHEN :layer IN ('L1','L2') THEN 'uncertain' ELSE 'stable' END, :expires_at
                )
                """
            ),
            {
                **params,
                "id": replacement_id,
                "statement": replacement,
                "layer": layer,
                "kind": correction_kind,
                "policy_version": policy_version,
                "subject_key": subject_key,
                "expires_at": owned_row["expires_at"],
            },
        )
        await self._add_evidence(connection, replacement_id, source_event_id)
        if is_summary:
            for source in rows:
                if not source["is_correction"]:
                    await self._add_evidence(connection, replacement_id, str(source["id"]))
            await connection.execute(
                text("INSERT INTO memory_dependency (memory_id, event_id) VALUES (:memory_id, :id)"),
                [{"memory_id": replacement_id, "id": source["id"]} for source in rows],
            )
        if subject_key and await self._link_unresolved_conflicts(
            connection,
            scope,
            replacement_id,
            subject_key,
            owned_row["kind"],
            replacement,
            1.0,
        ):
            await self._audit(connection, scope, "conflict_detected", replacement_id)
        await self._audit(connection, scope, audit_action, replacement_id)
        return replacement_id

    async def isolate_memories(
        self, scope: MemoryScope, memory_ids: list[str]
    ) -> tuple[list[str], str | None]:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            return await self._isolate_memories(connection, scope, memory_ids)

    async def _isolate_memories(
        self,
        connection: AsyncConnection,
        scope: MemoryScope,
        memory_ids: list[str],
    ) -> tuple[list[str], str | None]:
        normalized = sorted({str(UUID(value)) for value in memory_ids})
        await self._lock_deletion_target(connection, scope, "memory", normalized)
        existing = await self._find_or_retry_deletion_job(
            connection, scope, "memory_ids", normalized
        )
        if existing is not None:
            return [str(value) for value in existing["memory_ids"]], str(existing["id"])
        owned = await connection.execute(
            text(
                """
                SELECT id FROM memory_item
                WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                  AND id = ANY(CAST(:ids AS uuid[])) AND status <> 'deleting'
                ORDER BY id FOR UPDATE
                """
            ),
            {**scope.model_dump(), "ids": normalized},
        )
        if {str(row[0]) for row in owned} != set(normalized):
            raise NotFoundError("memory scope invalid or forbidden")
        rows = await connection.execute(
            text(
                """
                UPDATE memory_item SET status='deleting', deleted_at=now()
                WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                  AND id = ANY(CAST(:ids AS uuid[])) AND status <> 'deleting'
                RETURNING id
                """
            ),
            {**scope.model_dump(), "ids": normalized},
        )
        affected = [str(row[0]) for row in rows]
        if not affected:
            return [], None
        job_id = await self._create_deletion_job(connection, scope, affected, [])
        await self._audit(connection, scope, "forget", job_id)
        return affected, job_id

    async def prohibit_events(
        self, scope: MemoryScope, event_ids: list[str]
    ) -> tuple[list[str], str | None]:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            return await self._prohibit_events(connection, scope, event_ids)

    async def _prohibit_events(
        self,
        connection: AsyncConnection,
        scope: MemoryScope,
        event_ids: list[str],
    ) -> tuple[list[str], str | None]:
        normalized = sorted({str(UUID(value)) for value in event_ids})
        await self._lock_deletion_target(connection, scope, "event", normalized)
        existing = await self._find_or_retry_deletion_job(
            connection, scope, "event_ids", normalized
        )
        if existing is not None:
            return [str(value) for value in existing["memory_ids"]], str(existing["id"])
        params = {**scope.model_dump(), "ids": normalized}
        owned = await connection.execute(
            text(
                """
                SELECT id FROM memory_event
                WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                  AND id=ANY(CAST(:ids AS uuid[]))
                ORDER BY id FOR UPDATE
                """
            ),
            params,
        )
        if {str(row[0]) for row in owned} != set(normalized):
            raise NotFoundError("event scope invalid or forbidden")
        events = await connection.execute(
            text(
                """
                UPDATE memory_event SET prohibited_from_memory=true
                WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                  AND id = ANY(CAST(:ids AS uuid[]))
                  AND (NOT prohibited_from_memory OR redacted_at IS NULL)
                RETURNING id
                """
            ),
            params,
        )
        owned_event_ids = [str(row[0]) for row in events]
        if not owned_event_ids:
            return [], None
        params["ids"] = owned_event_ids
        await connection.execute(
            text(
                """
                UPDATE memory_outbox
                SET status='failed', completed_at=now(), locked_at=NULL,
                    error_code='prohibited_from_memory'
                WHERE event_id = ANY(CAST(:ids AS uuid[]))
                  AND status IN ('pending','processing','retry')
                """
            ),
            params,
        )
        evidence = await connection.execute(
            text(
                """
                WITH detached AS (
                    DELETE FROM memory_evidence
                    WHERE event_id = ANY(CAST(:ids AS uuid[]))
                    RETURNING memory_id
                )
                SELECT memory_id FROM detached
                UNION
                SELECT memory_id FROM memory_dependency
                WHERE event_id = ANY(CAST(:ids AS uuid[]))
                """
            ),
            params,
        )
        touched = sorted({str(row[0]) for row in evidence})
        affected: list[str] = []
        if touched:
            await connection.execute(
                text(
                    """
                    UPDATE memory_item m
                    SET affect=(
                        SELECT o.affect FROM memory_affect_observation o
                        JOIN memory_event e ON e.id=o.event_id
                        WHERE o.memory_id=m.id AND e.prohibited_from_memory=false
                          AND (e.tenant_id,e.user_id,e.agent_id)=(m.tenant_id,m.user_id,m.agent_id)
                        ORDER BY o.observed_at DESC, o.recorded_at DESC, o.event_id LIMIT 1
                    ),
                    expires_at=CASE WHEN m.layer='L1' THEN LEAST(m.expires_at, (
                        SELECT max(o.expires_at) FROM memory_affect_observation o
                        JOIN memory_event e ON e.id=o.event_id
                        WHERE o.memory_id=m.id AND e.prohibited_from_memory=false
                          AND (e.tenant_id,e.user_id,e.agent_id)=(m.tenant_id,m.user_id,m.agent_id)
                    )) ELSE m.expires_at END
                    WHERE m.id=ANY(CAST(:memory_ids AS uuid[]))
                      AND m.tenant_id=:tenant_id AND m.user_id=:user_id AND m.agent_id=:agent_id
                      AND m.affect IS NOT NULL
                    """
                ),
                {**scope.model_dump(), "memory_ids": touched},
            )
            memories = await connection.execute(
                text(
                    """
                    UPDATE memory_item m SET status='deleting', deleted_at=now()
                    WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                      AND m.agent_id=:agent_id
                      AND m.id = ANY(CAST(:memory_ids AS uuid[]))
                      AND (NOT EXISTS (
                          SELECT 1 FROM memory_evidence e WHERE e.memory_id=m.id
                      ) OR EXISTS (
                          SELECT 1 FROM memory_dependency dependency
                          WHERE dependency.memory_id=m.id
                            AND dependency.event_id=ANY(CAST(:ids AS uuid[]))
                      ))
                    RETURNING m.id
                    """
                ),
                {**params, "memory_ids": touched},
            )
            affected = [str(row[0]) for row in memories]
        job_id = await self._create_deletion_job(
            connection, scope, affected, owned_event_ids
        )
        await self._audit(connection, scope, "do_not_remember", job_id)
        return affected, job_id

    async def search(
        self,
        scope: MemoryScope,
        query: str,
        purpose: str,
        now: datetime,
        limit: int = 100,
        *,
        embedding_model: str | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[MemoryContextItem]:
        async with self.engine.connect() as connection:
            await self._set_scope(connection, scope)
            rows = await connection.execute(
                text(
                    """
                    WITH eligible AS (
                        SELECT m.*, observation.affect AS observed_affect,
                               observation.observed_at, observation.expires_at AS observed_expiry
                        FROM memory_item m
                        LEFT JOIN LATERAL (
                            SELECT o.affect, o.observed_at, o.expires_at
                            FROM memory_affect_observation o
                            JOIN memory_event e ON e.id=o.event_id
                            WHERE o.memory_id=m.id AND m.layer='L1'
                              AND (e.tenant_id,e.user_id,e.agent_id)=(m.tenant_id,m.user_id,m.agent_id)
                              AND o.observed_at<=:now AND o.recorded_at<=:now
                              AND e.occurred_at<=:now AND e.recorded_at<=:now
                              AND e.prohibited_from_memory=false
                            ORDER BY o.observed_at DESC, o.recorded_at DESC, o.event_id LIMIT 1
                        ) observation ON true
                        WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                          AND m.agent_id=:agent_id
                          AND m.created_at <= :now
                          AND cardinality(m.poisoning_flags)=0
                          AND m.sensitivity NOT IN ('high','secret')
                          AND (
                            m.status='active'
                            OR (:purpose='history' AND m.status IN ('superseded','archived'))
                            OR (:purpose='history' AND m.layer='L1' AND m.status='expired')
                          )
                          AND (
                            :purpose='history'
                            OR (
                              (m.valid_from IS NULL OR m.valid_from <= :now)
                              AND (m.valid_to IS NULL OR m.valid_to > :now)
                            )
                          )
                          AND (
                            :purpose='history'
                            OR COALESCE(observation.expires_at, m.expires_at) IS NULL
                            OR COALESCE(observation.expires_at, m.expires_at) > :now
                          )
                    ),
                    lexical AS (
                        SELECT id, row_number() OVER (
                            ORDER BY
                              (char_length(:query) >= 3 AND strpos(statement, :query) > 0) DESC,
                              ts_rank_cd(search_vector, websearch_to_tsquery('simple', :query))
                              + similarity(statement, :query) DESC,
                              updated_at DESC, id
                        ) AS position
                        FROM eligible
                        WHERE search_vector @@ websearch_to_tsquery('simple', :query)
                           OR similarity(statement, :query) > 0.05
                           OR (char_length(:query) >= 3 AND strpos(statement, :query) > 0)
                        ORDER BY
                          (char_length(:query) >= 3 AND strpos(statement, :query) > 0) DESC,
                          ts_rank_cd(search_vector, websearch_to_tsquery('simple', :query))
                          + similarity(statement, :query) DESC,
                          updated_at DESC, id
                        LIMIT 40
                    ),
                    exact_subject AS (
                        SELECT id, row_number() OVER (
                            ORDER BY (subject_key=:query) DESC,
                                     similarity(subject_key, :query) DESC, updated_at DESC, id
                        ) AS position
                        FROM eligible
                        WHERE subject_key IS NOT NULL
                          AND (subject_key=:query OR similarity(subject_key, :query) > 0.20)
                        ORDER BY (subject_key=:query) DESC,
                                 similarity(subject_key, :query) DESC, updated_at DESC, id
                        LIMIT 20
                    ),
                    recent_episode AS (
                        SELECT id, row_number() OVER (
                            ORDER BY COALESCE(valid_from, updated_at) DESC, id
                        ) AS position
                        FROM eligible
                        WHERE layer='L2'
                        ORDER BY COALESCE(valid_from, updated_at) DESC, id
                        LIMIT 20
                    ),
                    purpose_context AS (
                        SELECT id, row_number() OVER (
                            ORDER BY affect_priority DESC, updated_at DESC, id
                        ) AS position
                        FROM (
                            SELECT id, updated_at, CASE WHEN :purpose='support' THEN
                                COALESCE(
                                    (observed_affect->>'arousal')::double precision
                                    * (observed_affect->>'confidence')::double precision
                                    * power(0.5, extract(epoch FROM (
                                        CAST(:now AS timestamptz) - observed_at
                                    )) / 21600.0),
                                    0
                                )
                                ELSE 0 END AS affect_priority
                            FROM eligible
                            WHERE (layer='L1' AND :purpose='support')
                               OR (layer='L3' AND :purpose='history')
                        ) ranked_context
                        ORDER BY affect_priority DESC, updated_at DESC, id
                        LIMIT 20
                    ),
                    semantic_scores AS (
                        SELECT m.id, similarity.score
                        FROM eligible m
                        JOIN memory_embedding e ON e.memory_id=m.id
                        CROSS JOIN LATERAL (
                            SELECT sum(a::double precision * b)
                                / NULLIF(sqrt(sum(a::double precision * a)
                                              * sum(b::double precision * b)), 0) AS score
                            FROM unnest(e.embedding, CAST(:query_embedding AS real[])) AS pairs(a,b)
                        ) similarity
                        WHERE e.model=:embedding_model AND e.dimensions=:embedding_dimensions
                          AND m.status='active' AND m.sensitivity IN ('low','medium')
                          AND cardinality(m.poisoning_flags)=0
                    ),
                    semantic AS (
                        SELECT id, row_number() OVER (ORDER BY score DESC, id) AS position
                        FROM semantic_scores
                        WHERE score BETWEEN 0.5 AND 1.000001
                        ORDER BY score DESC, id LIMIT 40
                    ),
                    ranked AS (
                        SELECT id, 1.0 / (60 + position) AS score, true AS query_matched FROM lexical
                        UNION ALL
                        SELECT id, 1.0 / (60 + position), true FROM exact_subject
                        UNION ALL
                        SELECT id, 1.0 / (60 + position), true FROM semantic
                        UNION ALL
                        SELECT id, 1.0 / (60 + position), false FROM recent_episode
                        UNION ALL
                        SELECT id, 1.0 / (60 + position), false FROM purpose_context
                    ),
                    fused AS (
                        SELECT id, SUM(score) AS rrf_score, bool_or(query_matched) AS query_matched
                        FROM ranked GROUP BY id
                    )
                    SELECT m.id, m.statement, m.layer, m.valid_from, m.valid_to,
                           m.confidence, COALESCE(state.observed_expiry, m.expires_at) AS expires_at,
                           CASE WHEN m.layer='L1' AND :purpose IN ('support','history') THEN
                               COALESCE(state.observed_affect,
                                   CASE WHEN (m.affect->>'observed_at')::timestamptz <= :now
                                        THEN m.affect END)
                           END AS affect,
                           COALESCE(array_agg(e.event_id::text)
                             FILTER (WHERE e.event_id IS NOT NULL), '{}') AS event_ids,
                           COALESCE(c.conflict_ids, '{}') AS conflict_ids,
                           f.query_matched,
                           (m.sensitivity='low' AND cardinality(m.poisoning_flags)=0
                            AND m.layer IN ('L2','L3')
                            AND m.subject_key IS DISTINCT FROM :query
                            AND cardinality(COALESCE(c.conflict_ids, '{}'))=0) AS rerank_eligible,
                           f.rrf_score
                           + (CASE m.future_utility
                               WHEN 'high' THEN 0.20
                               WHEN 'medium' THEN 0.08
                               ELSE 0
                             END
                             + CASE WHEN :purpose='support' AND m.layer='L1' THEN
                                 0.08 * COALESCE(
                                   (state.observed_affect->>'arousal')::double precision, 0
                                 ) * COALESCE(
                                   (state.observed_affect->>'confidence')::double precision, 0
                                 )
                               ELSE 0 END)
                             * CASE
                                 WHEN :purpose='history' OR m.layer NOT IN ('L1','L2') THEN 1
                                 ELSE power(
                                   0.5,
                                   GREATEST(0, extract(epoch FROM (
                                     CAST(:now AS timestamptz)
                                     - COALESCE(state.observed_at, CASE
                                         WHEN m.layer='L1'
                                           AND (m.affect->>'observed_at')::timestamptz <= :now
                                         THEN (m.affect->>'observed_at')::timestamptz
                                         ELSE COALESCE(m.valid_from, m.created_at)
                                       END)
                                   ))) / CASE WHEN m.layer='L1' THEN 21600.0 ELSE 2592000.0 END
                                 )
                               END
                           - CASE WHEN cardinality(c.conflict_ids) > 0 THEN 0.12 ELSE 0 END
                           + CASE WHEN :purpose IN ('conversation','task')
                                  AND f.query_matched AND m.sensitivity='low'
                                  AND cardinality(m.poisoning_flags)=0
                                  AND m.last_reinforced_at<=:now
                                  AND COALESCE(cardinality(c.conflict_ids), 0)=0
                             THEN 0.02 * power(
                                 0.5, extract(epoch FROM (
                                     CAST(:now AS timestamptz)-m.last_reinforced_at
                                 )) / 604800.0
                             )
                             ELSE 0 END
                           AS rank
                    FROM memory_item m
                    JOIN fused f ON f.id=m.id
                    JOIN eligible state ON state.id=m.id
                    LEFT JOIN memory_evidence e ON e.memory_id=m.id
                      AND EXISTS (
                        SELECT 1 FROM memory_event evidence_event
                        WHERE evidence_event.id=e.event_id
                          AND evidence_event.occurred_at <= :now
                          AND evidence_event.recorded_at <= :now
                          AND NOT evidence_event.prohibited_from_memory
                      )
                    LEFT JOIN LATERAL (
                        SELECT array_agg(
                            CASE WHEN r.from_memory_id=m.id
                                 THEN r.to_memory_id::text
                                 ELSE r.from_memory_id::text END
                            ORDER BY r.created_at
                        ) AS conflict_ids
                        FROM memory_relation r
                        JOIN eligible other ON other.id=CASE
                            WHEN r.from_memory_id=m.id THEN r.to_memory_id
                            ELSE r.from_memory_id
                        END
                        WHERE r.relation='contradicts'
                          AND (r.resolved_at IS NULL OR r.resolved_at > :now)
                          AND (r.from_memory_id=m.id OR r.to_memory_id=m.id)
                          AND other.status='active'
                          AND r.created_at <= :now
                    ) c ON true
                    GROUP BY m.id, f.rrf_score, f.query_matched, c.conflict_ids,
                             state.observed_affect, state.observed_at, state.observed_expiry
                    ORDER BY (m.subject_key IS NOT DISTINCT FROM :query) DESC,
                             f.query_matched DESC,
                             (char_length(:query) >= 3 AND strpos(m.statement, :query) > 0) DESC,
                             rank DESC, m.updated_at DESC, m.id
                    LIMIT :limit
                    """
                ),
                {
                    **scope.model_dump(),
                    "query": query,
                    "embedding_model": embedding_model,
                    "query_embedding": query_embedding or [],
                    "embedding_dimensions": len(query_embedding or []),
                    "purpose": purpose,
                    "now": now,
                    "limit": limit,
                },
            )
            return [self._context_item(row) for row in rows.mappings()]

    async def profile(
        self,
        scope: MemoryScope,
        now: datetime,
        limit: int = 50,
    ) -> list[MemoryContextItem]:
        async with self.engine.connect() as connection:
            await self._set_scope(connection, scope)
            rows = await connection.execute(
                text(
                    """
                    SELECT m.id, m.statement, m.layer, m.valid_from, m.valid_to,
                           m.confidence, m.expires_at,
                           NULL::jsonb AS affect,
                           COALESCE(array_agg(e.event_id::text)
                             FILTER (WHERE e.event_id IS NOT NULL), '{}') AS event_ids,
                           ARRAY[]::text[] AS conflict_ids
                    FROM memory_item m
                    LEFT JOIN memory_evidence e ON e.memory_id=m.id
                      AND EXISTS (
                        SELECT 1 FROM memory_event evidence_event
                        WHERE evidence_event.id=e.event_id
                          AND evidence_event.occurred_at <= :now
                          AND evidence_event.recorded_at <= :now
                          AND NOT evidence_event.prohibited_from_memory
                      )
                    WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                      AND m.agent_id=:agent_id
                      AND m.status='active' AND m.layer='L3'
                      AND m.created_at <= :now
                      AND m.sensitivity='low'
                      AND cardinality(m.poisoning_flags)=0
                      AND m.confidence >= 0.90
                      AND m.future_utility='high' AND m.durability='stable'
                      AND (m.valid_from IS NULL OR m.valid_from <= :now)
                      AND (m.valid_to IS NULL OR m.valid_to > :now)
                      AND (m.expires_at IS NULL OR m.expires_at > :now)
                      AND (
                        m.kind IN ('explicit','explicit_correction')
                        OR EXISTS (
                          SELECT 1
                          FROM memory_evidence source_evidence
                          JOIN memory_event source_event
                            ON source_event.id=source_evidence.event_id
                          WHERE source_evidence.memory_id=m.id
                            AND source_event.source='user'
                            AND source_event.prohibited_from_memory=false
                        )
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM memory_relation r
                        JOIN memory_item other ON other.id=CASE
                          WHEN r.from_memory_id=m.id THEN r.to_memory_id
                          ELSE r.from_memory_id
                        END
                        WHERE r.relation='contradicts'
                          AND (r.resolved_at IS NULL OR r.resolved_at > :now)
                          AND (r.from_memory_id=m.id OR r.to_memory_id=m.id)
                          AND other.status='active'
                          AND r.created_at <= :now AND other.created_at <= :now
                          AND (other.valid_from IS NULL OR other.valid_from <= :now)
                          AND (other.valid_to IS NULL OR other.valid_to > :now)
                          AND (other.expires_at IS NULL OR other.expires_at > :now)
                      )
                    GROUP BY m.id
                    ORDER BY
                      CASE WHEN m.kind IN ('explicit','explicit_correction') THEN 0 ELSE 1 END,
                      m.confidence DESC, m.updated_at DESC, m.id DESC
                    LIMIT :limit
                    """
                ),
                {**scope.model_dump(), "now": now, "limit": limit},
            )
            return [self._context_item(row) for row in rows.mappings()]

    async def list_memories(self, query: MemoryListQuery) -> Sequence[MemorySummary]:
        conditions = [
            "m.tenant_id=:tenant_id",
            "m.user_id=:user_id",
            "m.agent_id=:agent_id",
            "m.status <> 'deleting'",
            "m.status = ANY(CAST(:statuses AS text[]))",
        ]
        params: dict[str, Any] = {
            **query.scope.model_dump(),
            "statuses": query.statuses,
            "limit": query.limit,
        }
        if query.layers:
            conditions.append("m.layer = ANY(CAST(:layers AS text[]))")
            params["layers"] = [layer.value for layer in query.layers]
        if query.cursor:
            conditions.append("m.id < CAST(:cursor AS uuid)")
            params["cursor"] = query.cursor
        sql = f"""
            SELECT m.id, m.statement, m.layer, m.status, m.valid_from, m.valid_to,
                   m.expires_at, m.kind, m.confidence, m.future_utility,
                   CASE WHEN m.layer='L1' THEN m.affect END AS affect,
                   m.policy_version, m.subject_key, m.sensitivity,
                   m.poisoning_flags,
                   COALESCE(c.conflict_ids, '{{}}') AS conflict_ids,
                   COALESCE(array_agg(e.event_id::text)
                     FILTER (WHERE e.event_id IS NOT NULL), '{{}}') AS event_ids
            FROM memory_item m
            LEFT JOIN memory_evidence e ON e.memory_id=m.id
              AND EXISTS (
                SELECT 1 FROM memory_event evidence_event
                WHERE evidence_event.id=e.event_id AND NOT evidence_event.prohibited_from_memory
              )
            LEFT JOIN LATERAL (
                SELECT array_agg(
                    CASE WHEN r.from_memory_id=m.id
                         THEN r.to_memory_id::text
                         ELSE r.from_memory_id::text END
                    ORDER BY r.created_at
                ) AS conflict_ids
                FROM memory_relation r
                JOIN memory_item other ON other.id=CASE
                    WHEN r.from_memory_id=m.id THEN r.to_memory_id
                    ELSE r.from_memory_id
                END
                WHERE r.relation='contradicts'
                  AND r.resolved_at IS NULL
                  AND (r.from_memory_id=m.id OR r.to_memory_id=m.id)
                  AND other.status='active'
            ) c ON true
            WHERE {' AND '.join(conditions)}
            GROUP BY m.id, c.conflict_ids
            ORDER BY m.id DESC
            LIMIT :limit
        """
        async with self.engine.connect() as connection:
            await self._set_scope(connection, query.scope)
            rows = await connection.execute(text(sql), params)
            return [
                MemorySummary(
                    memory_id=str(row["id"]),
                    statement=row["statement"],
                    layer=MemoryLayer(row["layer"]),
                    status=row["status"],
                    valid_from=row["valid_from"],
                    valid_to=row["valid_to"],
                    expires_at=row["expires_at"],
                    source_event_ids=list(row["event_ids"]),
                    kind=row["kind"],
                    confidence=row["confidence"],
                    future_utility=row["future_utility"],
                    affect=row["affect"],
                    policy_version=row["policy_version"],
                    subject_key=row["subject_key"],
                    sensitivity=row["sensitivity"],
                    poisoning_flags=list(row["poisoning_flags"]),
                    conflict_memory_ids=list(row["conflict_ids"]),
                )
                for row in rows.mappings()
            ]

    async def deletion_status(self, scope: MemoryScope, job_id: str) -> DeletionStatus:
        async with self.engine.connect() as connection:
            await self._set_scope(connection, scope)
            row = await connection.execute(
                text(
                    """
                    SELECT id, status, attempts, error_code, created_at, completed_at
                    FROM deletion_job
                    WHERE id=:id AND tenant_id=:tenant_id AND user_id=:user_id
                      AND agent_id=:agent_id
                    """
                ),
                {"id": job_id, **scope.model_dump()},
            )
            result = row.mappings().first()
            if not result:
                raise NotFoundError(job_id)
            return DeletionStatus(
                job_id=str(result["id"]),
                status=result["status"],
                attempts=result["attempts"],
                error_code=result["error_code"],
                created_at=result["created_at"],
                completed_at=result["completed_at"],
            )

    async def affect_history(
        self, scope: MemoryScope, memory_id: str, limit: int, as_of: datetime | None = None
    ) -> list[AffectObservation]:
        async with self.engine.connect() as connection:
            await self._set_scope(connection, scope)
            rows = await connection.execute(
                text(
                    """
                    SELECT o.memory_id::text AS memory_id, o.event_id::text AS event_id,
                           o.affect, o.observed_at,
                           o.expires_at, o.recorded_at
                    FROM memory_affect_observation o
                    JOIN memory_item m ON m.id=o.memory_id
                    JOIN memory_event e ON e.id=o.event_id
                    WHERE m.id=:id AND m.tenant_id=:tenant_id AND m.user_id=:user_id
                      AND m.agent_id=:agent_id
                      AND m.status IN ('active','expired','archived','superseded')
                      AND m.layer='L1'
                      AND (e.tenant_id,e.user_id,e.agent_id)=(m.tenant_id,m.user_id,m.agent_id)
                      AND e.prohibited_from_memory=false
                      AND GREATEST(o.observed_at, o.recorded_at, e.occurred_at, e.recorded_at)
                          <= COALESCE(CAST(:as_of AS timestamptz), 'infinity'::timestamptz)
                    ORDER BY o.observed_at DESC, o.recorded_at DESC, o.event_id
                    LIMIT :limit
                    """
                ),
                {"id": memory_id, "limit": limit, "as_of": as_of, **scope.model_dump()},
            )
            return [AffectObservation.model_validate(row) for row in rows.mappings()]

    async def list_audit(self, scope: MemoryScope, limit: int) -> list[AuditEntry]:
        async with self.engine.connect() as connection:
            await self._set_scope(connection, scope)
            rows = await connection.execute(
                text(
                    """
                    SELECT id, action, target_id, created_at
                    FROM memory_audit
                    WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                {**scope.model_dump(), "limit": limit},
            )
            return [
                AuditEntry(
                    audit_id=str(row["id"]),
                    action=row["action"],
                    target_id=row["target_id"],
                    created_at=row["created_at"],
                )
                for row in rows.mappings()
            ]

    async def list_candidate_decisions(
        self, scope: MemoryScope, limit: int
    ) -> list[CandidateDecisionAudit]:
        async with self.engine.connect() as connection:
            await self._set_scope(connection, scope)
            rows = await connection.execute(
                text(
                    """
                    SELECT id, event_id, candidate_ordinal, proposed_layer, sensitivity,
                           CASE WHEN outcome='rejected' THEN 'redacted' ELSE kind END AS kind,
                           CASE WHEN outcome<>'rejected' THEN subject_key END AS subject_key,
                           CASE WHEN outcome='rejected' THEN ARRAY[]::text[]
                                ELSE poisoning_flags END AS poisoning_flags,
                           outcome, reason,
                           policy_version, stored_memory_id, created_at
                    FROM memory_candidate_decision d
                    WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                      AND EXISTS (
                        SELECT 1 FROM memory_event e
                        WHERE e.id=d.event_id AND NOT e.prohibited_from_memory
                      )
                      AND (
                        d.stored_memory_id IS NULL OR EXISTS (
                          SELECT 1 FROM memory_item m
                          WHERE m.id=d.stored_memory_id AND m.status<>'deleting'
                        )
                      )
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                {**scope.model_dump(), "limit": limit},
            )
            return [
                CandidateDecisionAudit(
                    decision_id=str(row["id"]),
                    event_id=str(row["event_id"]),
                    candidate_ordinal=row["candidate_ordinal"],
                    kind=row["kind"],
                    proposed_layer=MemoryLayer(row["proposed_layer"]),
                    subject_key=row["subject_key"],
                    sensitivity=row["sensitivity"],
                    poisoning_flags=list(row["poisoning_flags"]),
                    outcome=row["outcome"],
                    reason=row["reason"],
                    policy_version=row["policy_version"],
                    stored_memory_id=(
                        str(row["stored_memory_id"])
                        if row["stored_memory_id"] is not None
                        else None
                    ),
                    created_at=row["created_at"],
                )
                for row in rows.mappings()
            ]

    async def record_recall_diagnostic(
        self,
        scope: MemoryScope,
        diagnostics_id: str,
        purpose: str,
        token_budget: int,
        candidate_count: int,
        selected_memory_ids: list[str],
        truncated: bool,
        *,
        reinforced_memory_ids: Sequence[str] = (),
        recalled_at: datetime | None = None,
    ) -> None:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            reinforce = sorted(set(selected_memory_ids).intersection(reinforced_memory_ids))
            if reinforce and recalled_at is not None and purpose in {"conversation", "task"}:
                await connection.execute(
                    text("""
                        WITH eligible AS (
                            SELECT id FROM memory_item
                            WHERE id=ANY(CAST(:ids AS uuid[]))
                              AND tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                              AND status='active' AND layer IN ('L2','L3') AND sensitivity='low'
                              AND cardinality(poisoning_flags)=0
                              AND (valid_from IS NULL OR valid_from<=now())
                              AND (valid_to IS NULL OR valid_to>now())
                              AND (expires_at IS NULL OR expires_at>now())
                              AND CAST(:recalled_at AS timestamptz) BETWEEN now()-interval '5 minutes' AND now()
                              AND (last_reinforced_at IS NULL OR last_reinforced_at<now()-interval '1 hour')
                            ORDER BY id FOR NO KEY UPDATE
                        )
                        UPDATE memory_item m SET last_reinforced_at=now()
                        FROM eligible WHERE m.id=eligible.id
                    """),
                    {**scope.model_dump(), "ids": reinforce, "recalled_at": recalled_at},
                )
            if selected_memory_ids:
                existing = await connection.execute(
                    text(
                        """
                        SELECT id FROM memory_item
                        WHERE id = ANY(CAST(:ids AS uuid[])) AND status <> 'deleting'
                          AND tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                        ORDER BY id FOR KEY SHARE
                        """
                    ),
                    {"ids": selected_memory_ids, **scope.model_dump()},
                )
                visible = {str(value) for value in existing.scalars()}
                selected_memory_ids = [value for value in selected_memory_ids if value in visible]
            await connection.execute(
                text(
                    """
                    INSERT INTO memory_recall_diagnostic (
                        id, tenant_id, user_id, agent_id, purpose,
                        token_budget, candidate_count, selected_memory_ids, truncated
                    ) VALUES (
                        :id, :tenant_id, :user_id, :agent_id, :purpose,
                        :token_budget, :candidate_count,
                        CAST(:selected_memory_ids AS uuid[]), :truncated
                    )
                    """
                ),
                {
                    "id": diagnostics_id,
                    "purpose": purpose,
                    "token_budget": token_budget,
                    "candidate_count": candidate_count,
                    "selected_memory_ids": selected_memory_ids,
                    "truncated": truncated,
                    **scope.model_dump(),
                },
            )

    async def get_recall_diagnostic(
        self, scope: MemoryScope, diagnostics_id: str
    ) -> RecallDiagnostic:
        async with self.engine.connect() as connection:
            await self._set_scope(connection, scope)
            result = await connection.execute(
                text(
                    """
                    SELECT id, purpose, token_budget, candidate_count,
                           ARRAY(
                               SELECT selected.id
                               FROM unnest(selected_memory_ids) WITH ORDINALITY AS selected(id, rank)
                               JOIN memory_item m ON m.id=selected.id
                               WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                                 AND m.agent_id=:agent_id AND m.status<>'deleting'
                               ORDER BY selected.rank
                           ) AS selected_memory_ids, truncated, created_at
                    FROM memory_recall_diagnostic
                    WHERE id=:id AND tenant_id=:tenant_id AND user_id=:user_id
                      AND agent_id=:agent_id
                    """
                ),
                {"id": diagnostics_id, **scope.model_dump()},
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError(diagnostics_id)
            return RecallDiagnostic(
                diagnostics_id=str(row["id"]),
                purpose=row["purpose"],
                token_budget=row["token_budget"],
                candidate_count=row["candidate_count"],
                selected_memory_ids=[str(value) for value in row["selected_memory_ids"]],
                truncated=row["truncated"],
                created_at=row["created_at"],
            )

    async def resolve_memory_conflict_as_coexisting(
        self, scope: MemoryScope, memory_id: str, other_id: str,
    ) -> None:
        params = {**scope.model_dump(), "id": memory_id, "other_id": other_id}
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            await self._lock_memory_subject(connection, scope, memory_id)
            result = await connection.execute(
                text("""
                    WITH matching AS (
                        SELECT from_memory_id, to_memory_id FROM memory_relation
                        WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                          AND relation='contradicts'
                          AND ((from_memory_id=:id AND to_memory_id=:other_id)
                               OR (from_memory_id=:other_id AND to_memory_id=:id))
                    ), changed AS (
                        UPDATE memory_relation r SET resolved_at=now()
                        FROM matching m
                        WHERE r.from_memory_id=m.from_memory_id AND r.to_memory_id=m.to_memory_id
                          AND r.relation='contradicts' AND r.resolved_at IS NULL
                        RETURNING 1
                    )
                    SELECT EXISTS (SELECT 1 FROM matching), EXISTS (SELECT 1 FROM changed)
                """),
                params,
            )
            found, changed = result.one()
            if not found:
                raise NotFoundError(memory_id)
            if changed:
                await self._audit(connection, scope, "conflict_coexistence", memory_id)

    async def review_memory(
        self, scope: MemoryScope, memory_id: str, decision: str
    ) -> MemoryReviewReceipt:
        new_status = "active" if decision == "approve" else "rejected"
        audit_action = f"review_{decision}"
        params = {"id": memory_id, "status": new_status, **scope.model_dump()}
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            subject_key = (
                await self._lock_memory_subject(connection, scope, memory_id)
                if new_status == "active" else None
            )
            row = await connection.execute(
                text(
                    """
                    UPDATE memory_item
                    SET status=:status, updated_at=now()
                    WHERE id=:id AND tenant_id=:tenant_id AND user_id=:user_id
                      AND agent_id=:agent_id AND status='quarantined'
                      AND (
                        :status='rejected'
                        OR (cardinality(poisoning_flags)=0 AND sensitivity NOT IN ('high','secret'))
                      )
                    RETURNING id, subject_key, kind, statement, confidence
                    """
                ),
                params,
            )
            reviewed = row.mappings().first()
            if reviewed is None:
                replay = await connection.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM memory_item m
                            WHERE m.id=:id AND m.tenant_id=:tenant_id AND m.user_id=:user_id
                              AND m.agent_id=:agent_id AND m.status=:status
                              AND (:status='rejected' OR (
                                  cardinality(m.poisoning_flags)=0
                                  AND m.sensitivity NOT IN ('high','secret')
                              ))
                              AND :action = (
                                  SELECT a.action FROM memory_audit a
                                  WHERE a.tenant_id=m.tenant_id AND a.user_id=m.user_id
                                    AND a.agent_id=m.agent_id AND a.target_id=m.id::text
                                    AND a.action IN ('review_approve','review_reject')
                                  ORDER BY a.created_at DESC, a.id DESC LIMIT 1
                              )
                        )
                        """
                    ),
                    {**params, "action": audit_action},
                )
                if replay.scalar_one():
                    return MemoryReviewReceipt(memory_id=memory_id, status=new_status)
                raise NotFoundError(memory_id)
            if new_status == "active" and reviewed["subject_key"]:
                if reviewed["subject_key"] != subject_key:
                    raise MemorySourcesChanged()
                if await self._link_unresolved_conflicts(
                    connection,
                    scope,
                    memory_id,
                    reviewed["subject_key"],
                    reviewed["kind"],
                    reviewed["statement"],
                    reviewed["confidence"],
                ):
                    await self._audit(connection, scope, "conflict_detected", memory_id)
            await self._audit(connection, scope, audit_action, memory_id)
        return MemoryReviewReceipt(memory_id=memory_id, status=new_status)

    async def process_one_deletion(self) -> bool:
        async with self.engine.begin() as connection:
            claimed = await connection.execute(
                text(
                    """
                    WITH picked AS (
                        SELECT id FROM deletion_job
                        WHERE attempts < 5 AND (
                            (status IN ('pending','retry') AND available_at <= now())
                            OR (status='processing' AND locked_at < now() - interval '5 minutes')
                        )
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED LIMIT 1
                    )
                    UPDATE deletion_job d
                    SET status='processing', locked_at=now(), attempts=d.attempts + 1
                    FROM picked WHERE d.id=picked.id
                    RETURNING d.id, d.memory_ids, d.event_ids, d.attempts
                    """
                )
            )
            row = claimed.mappings().first()
        if not row:
            return False
        try:
            return await self._apply_deletion(row)
        except Exception as exc:
            await self._fail_deletion(str(row["id"]), row["attempts"], type(exc).__name__)
            raise

    async def _apply_deletion(self, row: Any) -> bool:
        async with self.engine.begin() as connection:
            lease = await connection.execute(
                text(
                    "SELECT id FROM deletion_job WHERE id=:id "
                    "AND status='processing' AND attempts=:attempts FOR UPDATE"
                ),
                {"id": row["id"], "attempts": row["attempts"]},
            )
            if lease.first() is None:
                return False
            await connection.execute(
                text(
                    "DELETE FROM memory_affect_observation "
                    "WHERE event_id=ANY(CAST(:event_ids AS uuid[]))"
                ),
                {"event_ids": row["event_ids"]},
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM memory_candidate_decision
                    WHERE event_id = ANY(CAST(:event_ids AS uuid[]))
                       OR stored_memory_id = ANY(CAST(:memory_ids AS uuid[]))
                    """
                ),
                {"event_ids": row["event_ids"], "memory_ids": row["memory_ids"]},
            )
            await connection.execute(
                text("DELETE FROM memory_item WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": row["memory_ids"]},
            )
            await connection.execute(
                text("DELETE FROM memory_recall_diagnostic "
                     "WHERE selected_memory_ids && CAST(:ids AS uuid[])"),
                {"ids": row["memory_ids"]},
            )
            await connection.execute(
                text(
                    """
                    UPDATE memory_event
                    SET content='[deleted-by-user]', metadata='{}'::jsonb,
                        ciphertext=NULL, ciphertext_key_id=NULL, ciphertext_retry_at=NULL,
                        occurred_utc_offset_seconds=NULL, background_eligible_at=NULL,
                        prohibited_from_memory=true, redacted_at=now()
                    WHERE id = ANY(CAST(:ids AS uuid[]))
                    """
                ),
                {"ids": row["event_ids"]},
            )
            await connection.execute(
                text("DELETE FROM memory_consolidation_run WHERE source_ids && CAST(:ids AS uuid[])"),
                {"ids": row["event_ids"]},
            )
            canary = await connection.execute(
                text(
                    """
                    SELECT
                      EXISTS (
                        SELECT 1 FROM memory_item
                        WHERE id = ANY(CAST(:memory_ids AS uuid[]))
                      )
                      OR EXISTS (
                        SELECT 1 FROM memory_embedding
                        WHERE memory_id = ANY(CAST(:memory_ids AS uuid[]))
                      )
                      OR EXISTS (
                        SELECT 1 FROM memory_evidence
                        WHERE memory_id = ANY(CAST(:memory_ids AS uuid[]))
                           OR event_id = ANY(CAST(:event_ids AS uuid[]))
                      )
                      OR EXISTS (
                        SELECT 1 FROM memory_dependency
                        WHERE memory_id = ANY(CAST(:memory_ids AS uuid[]))
                           OR event_id = ANY(CAST(:event_ids AS uuid[]))
                      )
                      OR EXISTS (
                        SELECT 1 FROM memory_relation
                        WHERE from_memory_id = ANY(CAST(:memory_ids AS uuid[]))
                           OR to_memory_id = ANY(CAST(:memory_ids AS uuid[]))
                      )
                      OR EXISTS (
                        SELECT 1 FROM memory_affect_observation
                        WHERE memory_id=ANY(CAST(:memory_ids AS uuid[]))
                           OR event_id=ANY(CAST(:event_ids AS uuid[]))
                      )
                      OR EXISTS (
                        SELECT 1 FROM memory_recall_diagnostic
                        WHERE selected_memory_ids && CAST(:memory_ids AS uuid[])
                      )
                      OR EXISTS (
                        SELECT 1 FROM memory_candidate_decision
                        WHERE event_id = ANY(CAST(:event_ids AS uuid[]))
                           OR stored_memory_id = ANY(CAST(:memory_ids AS uuid[]))
                      )
                      OR EXISTS (
                        SELECT 1 FROM memory_consolidation_run
                        WHERE source_ids && CAST(:event_ids AS uuid[])
                      )
                      OR EXISTS (
                        SELECT 1 FROM memory_event
                        WHERE id = ANY(CAST(:event_ids AS uuid[]))
                          AND (
                            content <> '[deleted-by-user]'
                            OR metadata <> '{}'::jsonb
                            OR ciphertext IS NOT NULL
                            OR prohibited_from_memory=false
                            OR redacted_at IS NULL
                            OR occurred_utc_offset_seconds IS NOT NULL
                            OR background_eligible_at IS NOT NULL
                          )
                      ) AS failed
                    """
                ),
                {
                    "memory_ids": row["memory_ids"],
                    "event_ids": row["event_ids"],
                },
            )
            if canary.scalar_one():
                raise DeletionCanaryFailed("deletion_canary_failed")
            await connection.execute(
                text(
                    """
                    UPDATE deletion_job
                    SET status='completed', completed_at=now(), locked_at=NULL, error_code=NULL
                    WHERE id=:id AND status='processing' AND attempts=:attempts
                    """
                ),
                {"id": row["id"], "attempts": row["attempts"]},
            )
            return True

    async def _fail_deletion(self, job_id: str, attempts: int, error_code: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE deletion_job
                    SET status=CASE WHEN attempts >= 5 THEN 'failed' ELSE 'retry' END,
                        available_at=now() + make_interval(secs => LEAST(300, attempts * attempts)),
                        locked_at=NULL, error_code=:error_code,
                        completed_at=CASE WHEN attempts >= 5 THEN now() ELSE NULL END
                    WHERE id=:id AND status='processing' AND attempts=:attempts
                    """
                ),
                {"id": job_id, "attempts": attempts, "error_code": error_code[:120]},
            )

    async def run_maintenance(self, raw_event_retention_days: int) -> None:
        async with self.engine.begin() as connection:
            leader = await connection.execute(
                text(
                    "SELECT pg_try_advisory_xact_lock("
                    "hashtextextended('persistent-memory-maintenance', 0))"
                )
            )
            if not leader.scalar_one():
                return
            for table in ("memory_outbox", "deletion_job"):
                await connection.execute(
                    text(
                        f"""
                        UPDATE {table}
                        SET status='failed', locked_at=NULL, completed_at=now(),
                            error_code='lease_exhausted'
                        WHERE status='processing' AND attempts >= 5
                          AND locked_at < now() - interval '5 minutes'
                        """
                    )
                )
            await connection.execute(
                text(
                    """
                    UPDATE memory_item
                    SET status=CASE WHEN layer='L2' THEN 'archived' ELSE 'expired' END,
                        updated_at=now()
                    WHERE status='active' AND expires_at IS NOT NULL AND expires_at <= now()
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    UPDATE memory_event e
                    SET content='[retention-expired]', metadata='{}'::jsonb,
                        ciphertext=NULL, ciphertext_key_id=NULL, ciphertext_retry_at=NULL,
                        occurred_utc_offset_seconds=NULL, background_eligible_at=NULL, redacted_at=now()
                    WHERE e.redacted_at IS NULL
                      AND e.recorded_at < now() - make_interval(days => :retention_days)
                      AND NOT EXISTS (
                        SELECT 1 FROM memory_outbox o
                        WHERE o.event_id=e.id AND o.status IN ('pending','processing','retry')
                      )
                    """
                ),
                {"retention_days": raw_event_retention_days},
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM memory_recall_diagnostic
                    WHERE created_at < now() - make_interval(days => :retention_days)
                    """
                ),
                {"retention_days": raw_event_retention_days},
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM memory_write_rate_limit
                    WHERE window_started_at < now() - interval '1 day'
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM memory_outbox
                    WHERE status IN ('completed','failed')
                      AND COALESCE(completed_at, created_at) < now() - interval '7 days'
                    """
                )
            )
            if self.event_cipher is not None:
                await self._backfill_event_ciphertext(connection, self.event_cipher)

    async def _backfill_event_ciphertext(
        self, connection: AsyncConnection, cipher: EventCipher
    ) -> None:
        eligibility = (
            "ciphertext_key_id IS DISTINCT FROM :key_id"
            if self.rotate_event_ciphertext else "ciphertext IS NULL"
        )
        events = await connection.execute(
            text(
                f"""
                SELECT id, content, metadata, ciphertext FROM memory_event
                WHERE {eligibility} AND redacted_at IS NULL
                  AND (ciphertext_retry_at IS NULL OR ciphertext_retry_at <= now())
                ORDER BY recorded_at, id
                FOR UPDATE SKIP LOCKED LIMIT 32
                """
            ),
            {"key_id": cipher.key_id},
        )
        encrypted = []
        failed_ids = []
        for event in events.mappings():
            try:
                payload = self._event_payload(str(event["id"]), event)
            except EventPayloadUnreadable:
                failed_ids.append(event["id"])
                continue
            encrypted.append({
                "id": event["id"],
                "ciphertext": cipher.encrypt(str(event["id"]), payload["content"], payload["metadata"]),
                "key_id": cipher.key_id,
            })
        if encrypted:
            await connection.execute(
                text(
                    "UPDATE memory_event SET content='', metadata='{}'::jsonb, "
                    "ciphertext=:ciphertext, ciphertext_key_id=:key_id, "
                    "ciphertext_retry_at=NULL WHERE id=:id"
                ),
                encrypted,
            )
        if failed_ids:
            await connection.execute(
                text(
                    "UPDATE memory_event SET ciphertext_retry_at=now() + interval '1 hour' "
                    "WHERE id=ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": failed_ids},
            )
            structlog.get_logger().warning("memory_event_rotation_deferred", count=len(failed_ids))

    @staticmethod
    async def _consume_write_quota(
        connection: Any,
        scope: MemoryScope,
        operation: str,
        limit: int,
    ) -> None:
        consumed = await connection.execute(
            text(
                """
                INSERT INTO memory_write_rate_limit (
                    tenant_id, user_id, agent_id, operation,
                    window_started_at, request_count
                ) VALUES (
                    :tenant_id, :user_id, :agent_id, :operation,
                    date_trunc('minute', statement_timestamp()), 1
                )
                ON CONFLICT (
                    tenant_id, user_id, agent_id, operation, window_started_at
                ) DO UPDATE
                SET request_count=memory_write_rate_limit.request_count + 1
                WHERE memory_write_rate_limit.request_count < :limit
                RETURNING request_count
                """
            ),
            {**scope.model_dump(), "operation": operation, "limit": limit},
        )
        if consumed.scalar_one_or_none() is None:
            raise RateLimitExceeded(operation)

    @staticmethod
    async def _add_evidence(connection: Any, memory_id: str, event_id: str) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO memory_evidence (memory_id, event_id)
                VALUES (:memory_id, :event_id)
                ON CONFLICT DO NOTHING
                """
            ),
            {"memory_id": memory_id, "event_id": event_id},
        )

    @staticmethod
    async def _set_scope(connection: Any, scope: MemoryScope) -> None:
        await connection.execute(
            text(
                """
                SELECT set_config('app.tenant_id', :tenant_id, true),
                       set_config('app.user_id', :user_id, true),
                       set_config('app.agent_id', :agent_id, true)
                """
            ),
            scope.model_dump(),
        )

    @staticmethod
    async def _audit(
        connection: Any, scope: MemoryScope, action: str, target_id: str
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO memory_audit (
                    id, tenant_id, user_id, agent_id, action, target_id
                ) VALUES (:id, :tenant_id, :user_id, :agent_id, :action, :target_id)
                """
            ),
            {"id": str(uuid4()), "action": action, "target_id": target_id, **scope.model_dump()},
        )

    async def _find_or_retry_deletion_job(
        self,
        connection: AsyncConnection,
        scope: MemoryScope,
        target_column: str,
        target_ids: list[str],
    ) -> Any | None:
        if target_column not in {"memory_ids", "event_ids"}:
            raise ValueError("invalid deletion target column")
        result = await connection.execute(
            text(
                f"""
                SELECT id, memory_ids, event_ids, status FROM deletion_job
                WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                  AND {target_column} @> CAST(:ids AS uuid[])
                  AND {target_column} <@ CAST(:ids AS uuid[])
                  AND (:by_event OR cardinality(event_ids)=0)
                ORDER BY created_at DESC, id DESC LIMIT 1
                """
            ),
            {**scope.model_dump(), "ids": target_ids, "by_event": target_column == "event_ids"},
        )
        row = result.mappings().first()
        if row is None or row["status"] == "completed":
            return None
        if row["status"] != "failed":
            return row
        memory_ids = [str(value) for value in row["memory_ids"]]
        event_ids = [str(value) for value in row["event_ids"]]
        job_id = await self._create_deletion_job(connection, scope, memory_ids, event_ids)
        await self._audit(connection, scope, "retry_deletion", job_id)
        return {"id": job_id, "memory_ids": memory_ids}

    @staticmethod
    async def _lock_deletion_target(
        connection: AsyncConnection,
        scope: MemoryScope,
        target_kind: str,
        target_ids: list[str],
    ) -> None:
        lock_key = json.dumps(
            [scope.tenant_id, scope.user_id, scope.agent_id, target_kind, *target_ids],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )

    @staticmethod
    async def _create_deletion_job(
        connection: Any,
        scope: MemoryScope,
        memory_ids: list[str],
        event_ids: list[str],
    ) -> str:
        job_id = str(uuid4())
        await connection.execute(
            text(
                """
                INSERT INTO deletion_job (
                    id, tenant_id, user_id, agent_id, memory_ids, event_ids, status
                ) VALUES (
                    :id, :tenant_id, :user_id, :agent_id,
                    CAST(:memory_ids AS uuid[]), CAST(:event_ids AS uuid[]), 'pending'
                )
                """
            ),
            {
                "id": job_id,
                "memory_ids": memory_ids,
                "event_ids": event_ids,
                **scope.model_dump(),
            },
        )
        return job_id

    @staticmethod
    def _context_item(row: Any) -> MemoryContextItem:
        return MemoryContextItem(
            memory_id=str(row["id"]),
            statement=row["statement"],
            layer=MemoryLayer(row["layer"]),
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            confidence=row["confidence"],
            source_event_ids=list(row["event_ids"]),
            conflict_memory_ids=list(row["conflict_ids"]),
            query_matched=row.get("query_matched", False),
            rerank_eligible=row.get("rerank_eligible", False),
            expires_at=row.get("expires_at"),
            affect=row.get("affect"),
        )

    @staticmethod
    async def _lock_memory_subject(
        connection: AsyncConnection, scope: MemoryScope, memory_id: str
    ) -> str | None:
        row = await connection.execute(
            text(
                """
                SELECT subject_key FROM memory_item
                WHERE id=:id AND tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                """
            ),
            {"id": memory_id, **scope.model_dump()},
        )
        subject_key = row.scalar_one_or_none()
        if subject_key:
            await PostgresRepository._lock_subject(connection, scope, subject_key)
        return subject_key

    @staticmethod
    async def _lock_subject(
        connection: AsyncConnection, scope: MemoryScope, subject_key: str
    ) -> None:
        lock_key = json.dumps(
            [scope.tenant_id, scope.user_id, scope.agent_id, subject_key],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )

    @staticmethod
    async def _link_unresolved_conflicts(
        connection: AsyncConnection,
        scope: MemoryScope,
        memory_id: str,
        subject_key: str,
        kind: str,
        statement: str,
        confidence: float,
    ) -> bool:
        inserted = await connection.execute(
            text(
                """
                INSERT INTO memory_relation (
                    tenant_id, user_id, agent_id, from_memory_id, to_memory_id,
                    relation, confidence
                )
                SELECT :tenant_id, :user_id, :agent_id, :memory_id, m.id,
                       'contradicts', LEAST(:confidence, m.confidence)
                FROM memory_item m
                JOIN memory_item incoming ON incoming.id=CAST(:memory_id AS uuid)
                WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                  AND m.agent_id=:agent_id AND m.id<>CAST(:memory_id AS uuid)
                  AND m.status='active' AND m.subject_key=:subject_key
                  AND incoming.layer='L3' AND m.layer='L3'
                  AND m.kind=:kind AND m.valid_to IS NULL
                  AND GREATEST(
                        COALESCE(m.valid_from, '-infinity'::timestamptz),
                        COALESCE(incoming.valid_from, '-infinity'::timestamptz)
                      ) < LEAST(
                        COALESCE(m.expires_at, 'infinity'::timestamptz),
                        COALESCE(incoming.valid_to, 'infinity'::timestamptz),
                        COALESCE(incoming.expires_at, 'infinity'::timestamptz)
                      )
                  AND rtrim(lower(regexp_replace(trim(m.statement), '\\s+', ' ', 'g')), '。！!. ')
                      <> rtrim(lower(regexp_replace(trim(:statement), '\\s+', ' ', 'g')), '。！!. ')
                  AND NOT EXISTS (
                      SELECT 1 FROM memory_relation r
                      WHERE r.relation='contradicts' AND r.resolved_at IS NOT NULL
                        AND ((r.from_memory_id=incoming.id AND r.to_memory_id=m.id)
                             OR (r.from_memory_id=m.id AND r.to_memory_id=incoming.id))
                  )
                ON CONFLICT DO NOTHING
                RETURNING to_memory_id
                """
            ),
            {
                **scope.model_dump(),
                "memory_id": memory_id,
                "subject_key": subject_key,
                "kind": kind,
                "statement": statement,
                "confidence": confidence,
            },
        )
        return inserted.first() is not None


def event_scope(event: InteractionEvent) -> MemoryScope:
    return MemoryScope(
        tenant_id=event.tenant_id,
        user_id=event.user_id,
        agent_id=event.agent_id,
    )
