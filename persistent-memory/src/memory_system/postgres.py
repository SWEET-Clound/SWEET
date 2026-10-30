from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from memory_system.models import (
    CommandReceipt,
    CorrectCommand,
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
    MemoryScope,
    MemorySummary,
    RecordReceipt,
    RememberCommand,
)


class IdempotencyConflictError(Exception):
    pass


class NotFoundError(Exception):
    pass


class PostgresRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    @classmethod
    def from_url(cls, database_url: str) -> PostgresRepository:
        return cls(create_async_engine(database_url, pool_pre_ping=True))

    async def close(self) -> None:
        await self.engine.dispose()

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

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
        params = {
            **event.model_dump(mode="json"),
            "id": event_id,
            "payload_hash": payload_hash,
            "metadata": json.dumps(event.metadata, ensure_ascii=False),
        }
        async with self.engine.begin() as connection:
            await self._set_scope(connection, event_scope(event))
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO memory_event (
                        id, tenant_id, user_id, agent_id, thread_id, idempotency_key,
                        payload_hash, source, content, occurred_at, metadata
                    ) VALUES (
                        :id, :tenant_id, :user_id, :agent_id, :thread_id, :idempotency_key,
                        :payload_hash, :source, :content, :occurred_at, CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (tenant_id, user_id, agent_id, idempotency_key) DO NOTHING
                    RETURNING id
                    """
                ),
                params,
            )
            row = inserted.mappings().first()
            if row:
                extraction_status = "not_applicable"
                if event.source is EventSource.USER and event.allow_memory_extraction:
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
                        WHERE kind='extract'
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
                           source, content, occurred_at, metadata
                    FROM memory_event WHERE id=:event_id
                    """
                ),
                {"event_id": job["event_id"]},
            )
            event = result.mappings().one()
            return EventJob(
                job_id=job["id"],
                event_id=str(job["event_id"]),
                attempts=job["attempts"],
                event=InteractionEvent.model_validate(event),
            )

    async def store_candidates(
        self,
        event_id: str,
        event: InteractionEvent,
        candidates: list[MemoryCandidate],
        policy_version: str,
        status: str = "active",
    ) -> list[str]:
        scope = event_scope(event)
        stored: list[str] = []
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            source = await connection.execute(
                text(
                    """
                    SELECT prohibited_from_memory FROM memory_event
                    WHERE id=:event_id AND tenant_id=:tenant_id AND user_id=:user_id
                      AND agent_id=:agent_id
                    FOR UPDATE
                    """
                ),
                {"event_id": event_id, **scope.model_dump()},
            )
            source_row = source.mappings().first()
            if source_row is None or source_row["prohibited_from_memory"]:
                return []
            for candidate in candidates:
                fingerprint = hashlib.sha256(
                    f"{candidate.kind}\0{' '.join(candidate.statement.lower().split())}".encode()
                ).hexdigest()
                memory_id = str(uuid4())
                affect = (
                    candidate.affect.model_dump_json() if candidate.affect is not None else None
                )
                row = await connection.execute(
                    text(
                        """
                        INSERT INTO memory_item (
                            id, tenant_id, user_id, agent_id, statement, layer, kind,
                            status, confidence, policy_version, valid_from, valid_to,
                            expires_at, fingerprint, future_utility, durability, affect
                        ) VALUES (
                            :id, :tenant_id, :user_id, :agent_id, :statement, :layer, :kind,
                            :status, :confidence, :policy_version, :valid_from, :valid_to,
                            :expires_at, :fingerprint, :future_utility, :durability,
                            CAST(:affect AS jsonb)
                        )
                        ON CONFLICT (tenant_id, user_id, agent_id, fingerprint)
                        WHERE fingerprint IS NOT NULL
                        DO UPDATE SET
                            confidence=GREATEST(memory_item.confidence, EXCLUDED.confidence),
                            expires_at=GREATEST(memory_item.expires_at, EXCLUDED.expires_at),
                            status=CASE
                                WHEN memory_item.status='quarantined'
                                  AND EXCLUDED.status='active' THEN 'active'
                                ELSE memory_item.status
                            END,
                            updated_at=now()
                        RETURNING id
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
                        **scope.model_dump(),
                    },
                )
                stored_id = str(row.scalar_one())
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
                stored.append(stored_id)
        return stored

    async def complete_extraction(self, job_id: int) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE memory_outbox
                    SET status='completed', completed_at=now(), locked_at=NULL, error_code=NULL
                    WHERE id=:id AND status='processing'
                    """
                ),
                {"id": job_id},
            )

    async def fail_extraction(self, job_id: int, error_code: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE memory_outbox
                    SET status=CASE WHEN attempts >= 5 THEN 'failed' ELSE 'retry' END,
                        available_at=now() + make_interval(secs => LEAST(300, attempts * attempts)),
                        locked_at=NULL,
                        error_code=:error_code
                    WHERE id=:id AND status='processing'
                    """
                ),
                {"id": job_id, "error_code": error_code[:120]},
            )

    async def execute_command_idempotent(
        self,
        command: MemoryCommand,
        key: str,
        payload_hash: str,
        policy_version: str,
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

            command_id = str(uuid4())
            if isinstance(command, RememberCommand):
                memory_id = await self._remember(
                    connection,
                    scope,
                    command.statement,
                    command.source_event_id,
                    policy_version,
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
        memory_id = str(uuid4())
        fingerprint = hashlib.sha256(
            f"explicit\0{' '.join(statement.lower().split())}".encode()
        ).hexdigest()
        event = await connection.execute(
            text(
                """
                SELECT id FROM memory_event
                WHERE id=:event_id AND tenant_id=:tenant_id AND user_id=:user_id
                  AND agent_id=:agent_id AND prohibited_from_memory=false
                FOR UPDATE
                """
            ),
            {"event_id": source_event_id, **scope.model_dump()},
        )
        if not event.first():
            raise NotFoundError(source_event_id)
        result = await connection.execute(
            text(
                """
                INSERT INTO memory_item (
                    id, tenant_id, user_id, agent_id, statement, layer, kind,
                    status, confidence, policy_version, valid_from, fingerprint,
                    future_utility, durability
                ) VALUES (
                    :id, :tenant_id, :user_id, :agent_id, :statement, 'L3',
                    'explicit', 'active', 1.0, :policy_version, now(), :fingerprint,
                    'high', 'stable'
                )
                ON CONFLICT (tenant_id, user_id, agent_id, fingerprint)
                WHERE fingerprint IS NOT NULL
                DO UPDATE SET
                    status=CASE
                        WHEN memory_item.status='quarantined' THEN 'active'
                        ELSE memory_item.status
                    END,
                    confidence=CASE
                        WHEN memory_item.status='quarantined' THEN 1.0
                        ELSE memory_item.confidence
                    END,
                    updated_at=now()
                RETURNING id
                """
            ),
            {
                "id": memory_id,
                "statement": statement,
                "policy_version": policy_version,
                "fingerprint": fingerprint,
                **scope.model_dump(),
            },
        )
        stored_id = str(result.scalar_one())
        await self._add_evidence(connection, stored_id, source_event_id)
        await self._audit(connection, scope, "remember", stored_id)
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
            )

    async def _correct(
        self,
        connection: AsyncConnection,
        scope: MemoryScope,
        memory_id: str,
        replacement: str,
        source_event_id: str,
        policy_version: str,
    ) -> str:
        replacement_id = str(uuid4())
        params = {"memory_id": memory_id, "event_id": source_event_id, **scope.model_dump()}
        owned = await connection.execute(
            text(
                """
                SELECT id FROM memory_item
                WHERE id=:memory_id AND tenant_id=:tenant_id AND user_id=:user_id
                  AND agent_id=:agent_id AND status='active'
                FOR UPDATE
                """
            ),
            params,
        )
        event = await connection.execute(
            text(
                """
                SELECT id FROM memory_event
                WHERE id=:event_id AND tenant_id=:tenant_id AND user_id=:user_id
                  AND agent_id=:agent_id AND prohibited_from_memory=false
                """
            ),
            params,
        )
        if not owned.first() or not event.first():
            raise NotFoundError(memory_id)
        await connection.execute(
            text("UPDATE memory_item SET status='superseded', valid_to=now() WHERE id=:memory_id"),
            params,
        )
        await connection.execute(
            text(
                """
                INSERT INTO memory_item (
                    id, tenant_id, user_id, agent_id, statement, layer, kind,
                    status, confidence, policy_version, valid_from, supersedes_id
                ) VALUES (
                    :id, :tenant_id, :user_id, :agent_id, :statement, 'L3',
                    'explicit_correction', 'active', 1.0, :policy_version, now(), :memory_id
                )
                """
            ),
            {
                **params,
                "id": replacement_id,
                "statement": replacement,
                "policy_version": policy_version,
            },
        )
        await self._add_evidence(connection, replacement_id, source_event_id)
        await self._audit(connection, scope, "correct", replacement_id)
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
        normalized = sorted(set(memory_ids))
        await self._lock_deletion_target(connection, scope, "memory", normalized)
        existing = await self._find_active_deletion_job(
            connection, scope, "memory_ids", normalized
        )
        if existing is not None:
            return [str(value) for value in existing["memory_ids"]], str(existing["id"])
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
        normalized = sorted(set(event_ids))
        await self._lock_deletion_target(connection, scope, "event", normalized)
        existing = await self._find_active_deletion_job(
            connection, scope, "event_ids", normalized
        )
        if existing is not None:
            return [str(value) for value in existing["memory_ids"]], str(existing["id"])
        params = {**scope.model_dump(), "ids": normalized}
        events = await connection.execute(
            text(
                """
                UPDATE memory_event SET prohibited_from_memory=true
                WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                  AND id = ANY(CAST(:ids AS uuid[])) AND prohibited_from_memory=false
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
                DELETE FROM memory_evidence
                WHERE event_id = ANY(CAST(:ids AS uuid[]))
                RETURNING memory_id
                """
            ),
            params,
        )
        touched = sorted({str(row[0]) for row in evidence})
        affected: list[str] = []
        if touched:
            memories = await connection.execute(
                text(
                    """
                    UPDATE memory_item m SET status='deleting', deleted_at=now()
                    WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                      AND m.agent_id=:agent_id
                      AND m.id = ANY(CAST(:memory_ids AS uuid[]))
                      AND NOT EXISTS (
                          SELECT 1 FROM memory_evidence e WHERE e.memory_id=m.id
                      )
                    RETURNING m.id
                    """
                ),
                {**scope.model_dump(), "memory_ids": touched},
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
    ) -> list[MemoryContextItem]:
        async with self.engine.connect() as connection:
            await self._set_scope(connection, scope)
            rows = await connection.execute(
                text(
                    """
                    SELECT m.id, m.statement, m.layer, m.valid_from, m.valid_to,
                           m.confidence,
                           COALESCE(array_agg(e.event_id::text)
                             FILTER (WHERE e.event_id IS NOT NULL), '{}') AS event_ids,
                           ts_rank_cd(m.search_vector, websearch_to_tsquery('simple', :query))
                           + similarity(m.statement, :query)
                           + CASE m.future_utility
                               WHEN 'high' THEN 0.20
                               WHEN 'medium' THEN 0.08
                               ELSE 0
                             END
                           + CASE WHEN m.layer='L3' THEN 0.20 ELSE 0 END
                           + CASE WHEN m.layer='L1' AND :purpose='support' THEN 0.10 ELSE 0 END
                           AS rank
                    FROM memory_item m
                    LEFT JOIN memory_evidence e ON e.memory_id=m.id
                    WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                      AND m.agent_id=:agent_id
                      AND (
                        m.status='active'
                        OR (:purpose='history' AND m.status='superseded')
                      )
                      AND (
                        :purpose='history'
                        OR (
                          (m.valid_from IS NULL OR m.valid_from <= :now)
                          AND (m.valid_to IS NULL OR m.valid_to > :now)
                        )
                      )
                      AND (m.expires_at IS NULL OR m.expires_at > :now)
                      AND (
                        m.search_vector @@ websearch_to_tsquery('simple', :query)
                        OR similarity(m.statement, :query) > 0.05
                        OR m.layer='L3'
                        OR (m.layer='L1' AND :purpose='support')
                      )
                    GROUP BY m.id
                    ORDER BY rank DESC, m.updated_at DESC
                    LIMIT :limit
                    """
                ),
                {
                    **scope.model_dump(),
                    "query": query,
                    "purpose": purpose,
                    "now": now,
                    "limit": limit,
                },
            )
            return [self._context_item(row) for row in rows.mappings()]

    async def list_memories(self, query: MemoryListQuery) -> Sequence[MemorySummary]:
        conditions = [
            "m.tenant_id=:tenant_id",
            "m.user_id=:user_id",
            "m.agent_id=:agent_id",
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
                   m.affect, m.policy_version,
                   COALESCE(array_agg(e.event_id::text)
                     FILTER (WHERE e.event_id IS NOT NULL), '{{}}') AS event_ids
            FROM memory_item m
            LEFT JOIN memory_evidence e ON e.memory_id=m.id
            WHERE {' AND '.join(conditions)}
            GROUP BY m.id
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

    async def process_one_deletion(self) -> bool:
        async with self.engine.begin() as connection:
            claimed = await connection.execute(
                text(
                    """
                    WITH picked AS (
                        SELECT id FROM deletion_job
                        WHERE (
                            (status IN ('pending','retry') AND available_at <= now())
                            OR (status='processing' AND locked_at < now() - interval '5 minutes')
                        )
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED LIMIT 1
                    )
                    UPDATE deletion_job d
                    SET status='processing', locked_at=now(), attempts=d.attempts + 1
                    FROM picked WHERE d.id=picked.id
                    RETURNING d.id, d.memory_ids, d.event_ids
                    """
                )
            )
            row = claimed.mappings().first()
        if not row:
            return False
        try:
            await self._apply_deletion(row)
        except Exception as exc:
            await self._fail_deletion(str(row["id"]), type(exc).__name__)
            raise
        return True

    async def _apply_deletion(self, row: Any) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM memory_item WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": row["memory_ids"]},
            )
            await connection.execute(
                text(
                    """
                    UPDATE memory_event
                    SET content='[deleted-by-user]', metadata='{}'::jsonb,
                        prohibited_from_memory=true, redacted_at=now()
                    WHERE id = ANY(CAST(:ids AS uuid[]))
                    """
                ),
                {"ids": row["event_ids"]},
            )
            await connection.execute(
                text(
                    """
                    UPDATE deletion_job
                    SET status='completed', completed_at=now(), locked_at=NULL, error_code=NULL
                    WHERE id=:id AND status='processing'
                    """
                ),
                {"id": row["id"]},
            )

    async def _fail_deletion(self, job_id: str, error_code: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE deletion_job
                    SET status=CASE WHEN attempts >= 5 THEN 'failed' ELSE 'retry' END,
                        available_at=now() + make_interval(secs => LEAST(300, attempts * attempts)),
                        locked_at=NULL, error_code=:error_code,
                        completed_at=CASE WHEN attempts >= 5 THEN now() ELSE NULL END
                    WHERE id=:id AND status='processing'
                    """
                ),
                {"id": job_id, "error_code": error_code[:120]},
            )

    async def run_maintenance(self, raw_event_retention_days: int) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE memory_item
                    SET status='expired', updated_at=now()
                    WHERE status='active' AND expires_at IS NOT NULL AND expires_at <= now()
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    UPDATE memory_event e
                    SET content='[retention-expired]', metadata='{}'::jsonb, redacted_at=now()
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
                    DELETE FROM memory_outbox
                    WHERE status IN ('completed','failed')
                      AND COALESCE(completed_at, created_at) < now() - interval '7 days'
                    """
                )
            )

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

    @staticmethod
    async def _find_active_deletion_job(
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
                SELECT id, memory_ids, event_ids FROM deletion_job
                WHERE tenant_id=:tenant_id AND user_id=:user_id AND agent_id=:agent_id
                  AND status IN ('pending','processing','retry')
                  AND {target_column} @> CAST(:ids AS uuid[])
                  AND {target_column} <@ CAST(:ids AS uuid[])
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {**scope.model_dump(), "ids": target_ids},
        )
        return result.mappings().first()

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
        )


def event_scope(event: InteractionEvent) -> MemoryScope:
    return MemoryScope(
        tenant_id=event.tenant_id,
        user_id=event.user_id,
        agent_id=event.agent_id,
    )
