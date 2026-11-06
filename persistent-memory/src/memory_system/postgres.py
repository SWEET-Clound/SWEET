from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

import structlog
from cryptography.exceptions import InvalidTag
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from memory_system.event_cipher import EventCipher
from memory_system.models import (
    AuditEntry,
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


class IdempotencyConflictError(Exception):
    pass


class NotFoundError(Exception):
    pass


class MemoryDeletionInProgress(Exception):
    pass


class DeletionCanaryFailed(RuntimeError):
    pass


class EventPayloadUnreadable(RuntimeError):
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
                           memory_event.ciphertext_retry_at
                    FROM memory_event, memory_item, memory_outbox, memory_evidence,
                         command_dedup, deletion_job, memory_relation, memory_audit,
                         memory_candidate_decision, memory_recall_diagnostic,
                         memory_write_rate_limit, memory_embedding
                    LIMIT 0
                    """
                )
            )

    @asynccontextmanager
    async def embedding_backfill_lock(self, model: str, dimensions: int) -> AsyncIterator[bool]:
        key = f"persistent-memory-embedding:{dimensions}:{model}"
        async with self.engine.begin() as connection:
            lock = await connection.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": key},
            )
            yield lock.scalar_one()

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
        params = {
            **event.model_dump(mode="json"),
            "id": event_id,
            "payload_hash": payload_hash,
            "opted_out": opted_out,
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
                        payload_hash, source, content, occurred_at, metadata,
                        ciphertext, ciphertext_key_id, prohibited_from_memory
                    ) VALUES (
                        :id, :tenant_id, :user_id, :agent_id, :thread_id, :idempotency_key,
                        :payload_hash, :source, :content, :occurred_at, CAST(:metadata AS jsonb),
                        :ciphertext, :ciphertext_key_id, :opted_out
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
                if event.source is EventSource.USER and event.allow_memory_extraction and not opted_out:
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
                           source, content, occurred_at, metadata, ciphertext
                    FROM memory_event WHERE id=:event_id
                    """
                ),
                {"event_id": job["event_id"]},
            )
            event = result.mappings().one()
        event_id = str(job["event_id"])
        try:
            payload = InteractionEvent.model_validate(self._event_payload(event_id, event))
        except (InvalidTag, ValueError):
            await self.fail_extraction(job["id"], job["attempts"], "EventPayloadUnreadable")
            raise EventPayloadUnreadable("could not decode extraction event") from None
        return EventJob(
            job_id=job["id"], event_id=event_id, attempts=job["attempts"], event=payload
        )

    def _event_payload(self, event_id: str, row: Any) -> dict[str, Any]:
        payload = dict(row)
        ciphertext = payload.pop("ciphertext")
        if ciphertext is not None:
            if self.event_cipher is None:
                raise ValueError("event encryption key is required to read this event")
            payload.update(self.event_cipher.decrypt(event_id, ciphertext))
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
            for candidate in candidates:
                conflict_eligible = (
                    status == "active"
                    and event.source is EventSource.USER
                    and bool(candidate.subject_key)
                    and candidate.valid_to is None
                )
                if conflict_eligible:
                    await self._lock_subject(connection, scope, candidate.subject_key or "")
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
                                ELSE memory_item.status
                            END,
                            subject_key=COALESCE(memory_item.subject_key, EXCLUDED.subject_key),
                            sensitivity=EXCLUDED.sensitivity,
                            poisoning_flags=EXCLUDED.poisoning_flags,
                            updated_at=now()
                        WHERE memory_item.status <> 'deleting'
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
                        "subject_key": candidate.subject_key,
                        "sensitivity": candidate.sensitivity,
                        "poisoning_flags": candidate.poisoning_flags,
                        **scope.model_dump(),
                    },
                )
                returned_id = row.scalar_one_or_none()
                if returned_id is None:
                    raise MemoryDeletionInProgress()
                stored_id = str(returned_id)
                stored_by_fingerprint[(candidate.proposed_layer, fingerprint)] = stored_id
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
                if conflict_eligible and await self._link_unresolved_conflicts(
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
                        "kind": "redacted" if candidate_decision.outcome == "rejected" else candidate.kind,
                        "proposed_layer": candidate.proposed_layer.value,
                        "subject_key": (
                            None if candidate_decision.outcome == "rejected" else candidate.subject_key
                        ),
                        "sensitivity": candidate.sensitivity,
                        "poisoning_flags": (
                            [] if candidate_decision.outcome == "rejected" else candidate.poisoning_flags
                        ),
                        "outcome": candidate_decision.outcome,
                        "reason": candidate_decision.reason,
                        "policy_version": policy_version,
                        "stored_memory_id": (
                            None if candidate_decision.outcome == "rejected"
                            else stored_by_fingerprint.get((candidate.proposed_layer, fingerprint))
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
            elif isinstance(command, RememberProcedureCommand):
                memory_id = await self._remember_explicit(
                    connection,
                    scope,
                    command.statement,
                    command.source_event_id,
                    policy_version,
                    layer="L4",
                    kind="user_confirmed_procedure",
                    audit_action="remember_procedure",
                    require_user_source=True,
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
    ) -> str:
        memory_id = str(uuid4())
        fingerprint = hashlib.sha256(
            f"{kind}\0{' '.join(statement.lower().split())}".encode()
        ).hexdigest()
        event = await connection.execute(
            text(
                """
                SELECT source, content, ciphertext FROM memory_event
                WHERE id=:event_id AND tenant_id=:tenant_id AND user_id=:user_id
                  AND agent_id=:agent_id AND prohibited_from_memory=false
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
                    future_utility, durability
                ) VALUES (
                    :id, :tenant_id, :user_id, :agent_id, :statement, :layer,
                    :kind, 'active', 1.0, :policy_version, now(), :fingerprint,
                    'high', 'stable'
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
        owned = await connection.execute(
            text(
                """
                SELECT id, subject_key, kind, layer FROM memory_item
                WHERE id=:memory_id AND tenant_id=:tenant_id AND user_id=:user_id
                  AND agent_id=:agent_id AND status='active'
                  AND ((:procedural=true AND layer='L4')
                    OR (:procedural=false AND layer<>'L4'))
                FOR UPDATE
                """
            ),
            params,
        )
        event = await connection.execute(
            text(
                """
                SELECT source, content, ciphertext FROM memory_event
                WHERE id=:event_id AND tenant_id=:tenant_id AND user_id=:user_id
                  AND agent_id=:agent_id AND prohibited_from_memory=false
                  AND (:procedural=false OR source='user')
                """
            ),
            params,
        )
        owned_row = owned.mappings().first()
        row = event.mappings().first()
        evidence = self._event_payload(source_event_id, row) if row is not None else None
        if (
            owned_row is None
            or evidence is None
            or is_memory_opt_out(evidence["source"], evidence["content"])
        ):
            raise NotFoundError(memory_id)
        layer = owned_row["layer"]
        correction_kind = (
            "user_confirmed_procedure_correction"
            if procedural
            else "explicit_correction"
        )
        audit_action = "correct_procedure" if procedural else "correct"
        subject_key = owned_row["subject_key"]
        if subject_key:
            await self._lock_subject(connection, scope, subject_key)
        await connection.execute(
            text("UPDATE memory_item SET status='superseded', valid_to=now() WHERE id=:memory_id"),
            params,
        )
        if subject_key:
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
                    subject_key, future_utility, durability
                ) VALUES (
                    :id, :tenant_id, :user_id, :agent_id, :statement, :layer,
                    :kind, 'active', 1.0, :policy_version, now(),
                    :memory_id, :subject_key, 'high', 'stable'
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
            },
        )
        await self._add_evidence(connection, replacement_id, source_event_id)
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
        normalized = sorted(set(memory_ids))
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
                FOR UPDATE
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
        normalized = sorted(set(event_ids))
        await self._lock_deletion_target(connection, scope, "event", normalized)
        existing = await self._find_or_retry_deletion_job(
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
                        SELECT m.*
                        FROM memory_item m
                        WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                          AND m.agent_id=:agent_id
                          AND m.created_at <= :now
                          AND (
                            m.status='active'
                            OR (:purpose='history' AND m.status IN ('superseded','archived'))
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
                            OR m.expires_at IS NULL
                            OR m.expires_at > :now
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
                        SELECT id, row_number() OVER (ORDER BY updated_at DESC, id) AS position
                        FROM eligible
                        WHERE (layer='L1' AND :purpose='support')
                           OR (layer='L3' AND :purpose='history')
                        ORDER BY updated_at DESC, id
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
                           m.confidence,
                           COALESCE(array_agg(e.event_id::text)
                             FILTER (WHERE e.event_id IS NOT NULL), '{}') AS event_ids,
                           COALESCE(c.conflict_ids, '{}') AS conflict_ids,
                           f.query_matched,
                           (m.sensitivity='low' AND cardinality(m.poisoning_flags)=0
                            AND m.layer IN ('L2','L3')
                            AND m.subject_key IS DISTINCT FROM :query
                            AND cardinality(COALESCE(c.conflict_ids, '{}'))=0) AS rerank_eligible,
                           f.rrf_score
                           + CASE m.future_utility
                               WHEN 'high' THEN 0.20
                               WHEN 'medium' THEN 0.08
                               ELSE 0
                             END
                           - CASE WHEN cardinality(c.conflict_ids) > 0 THEN 0.12 ELSE 0 END
                           AS rank
                    FROM memory_item m
                    JOIN fused f ON f.id=m.id
                    LEFT JOIN memory_evidence e ON e.memory_id=m.id
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
                          AND (r.from_memory_id=m.id OR r.to_memory_id=m.id)
                          AND other.status='active'
                          AND r.created_at <= :now AND other.created_at <= :now
                          AND (
                            :purpose='history'
                            OR (
                              (other.valid_from IS NULL OR other.valid_from <= :now)
                              AND (other.valid_to IS NULL OR other.valid_to > :now)
                              AND (other.expires_at IS NULL OR other.expires_at > :now)
                            )
                          )
                    ) c ON true
                    GROUP BY m.id, f.rrf_score, f.query_matched, c.conflict_ids
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
                           m.confidence,
                           COALESCE(array_agg(e.event_id::text)
                             FILTER (WHERE e.event_id IS NOT NULL), '{}') AS event_ids,
                           ARRAY[]::text[] AS conflict_ids
                    FROM memory_item m
                    LEFT JOIN memory_evidence e ON e.memory_id=m.id
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
                   m.affect, m.policy_version, m.subject_key, m.sensitivity,
                   m.poisoning_flags,
                   COALESCE(c.conflict_ids, '{{}}') AS conflict_ids,
                   COALESCE(array_agg(e.event_id::text)
                     FILTER (WHERE e.event_id IS NOT NULL), '{{}}') AS event_ids
            FROM memory_item m
            LEFT JOIN memory_evidence e ON e.memory_id=m.id
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
    ) -> None:
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
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

    async def review_memory(
        self, scope: MemoryScope, memory_id: str, decision: str
    ) -> MemoryReviewReceipt:
        new_status = "active" if decision == "approve" else "rejected"
        async with self.engine.begin() as connection:
            await self._set_scope(connection, scope)
            row = await connection.execute(
                text(
                    """
                    UPDATE memory_item
                    SET status=:status, updated_at=now()
                    WHERE id=:id AND tenant_id=:tenant_id AND user_id=:user_id
                      AND agent_id=:agent_id AND status='quarantined'
                    RETURNING id, subject_key, kind, statement, confidence
                    """
                ),
                {"id": memory_id, "status": new_status, **scope.model_dump()},
            )
            reviewed = row.mappings().first()
            if reviewed is None:
                raise NotFoundError(memory_id)
            if new_status == "active" and reviewed["subject_key"]:
                await self._lock_subject(connection, scope, reviewed["subject_key"])
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
            await self._audit(connection, scope, f"review_{decision}", memory_id)
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
                        prohibited_from_memory=true, redacted_at=now()
                    WHERE id = ANY(CAST(:ids AS uuid[]))
                    """
                ),
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
                        SELECT 1 FROM memory_event
                        WHERE id = ANY(CAST(:event_ids AS uuid[]))
                          AND (
                            content <> '[deleted-by-user]'
                            OR metadata <> '{}'::jsonb
                            OR ciphertext IS NOT NULL
                            OR prohibited_from_memory=false
                            OR redacted_at IS NULL
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
                        redacted_at=now()
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
            except (InvalidTag, ValueError):
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
        )

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
        normalized = " ".join(statement.lower().split())
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
                WHERE m.tenant_id=:tenant_id AND m.user_id=:user_id
                  AND m.agent_id=:agent_id AND m.id<>CAST(:memory_id AS uuid)
                  AND m.status='active' AND m.subject_key=:subject_key
                  AND m.kind=:kind AND m.valid_to IS NULL
                  AND lower(regexp_replace(trim(m.statement), '\\s+', ' ', 'g'))<>:normalized
                ON CONFLICT DO NOTHING
                RETURNING to_memory_id
                """
            ),
            {
                **scope.model_dump(),
                "memory_id": memory_id,
                "subject_key": subject_key,
                "kind": kind,
                "normalized": normalized,
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
