import asyncio
from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

import httpx
from prometheus_client import Counter

from memory_system.embedding import EmbeddingClient
from memory_system.models import (
    AuditEntry,
    CandidateDecisionAudit,
    CommandReceipt,
    CorrectCommand,
    CorrectProcedureCommand,
    InteractionEvent,
    MemoryCommand,
    MemoryContext,
    MemoryListQuery,
    MemoryReviewReceipt,
    MemoryScope,
    MemorySummary,
    ProfileProjection,
    RecallDiagnostic,
    RecallRequest,
    RecordReceipt,
    RememberCommand,
    RememberProcedureCommand,
)
from memory_system.policy import MemoryPolicy
from memory_system.postgres import PostgresRepository
from memory_system.rerank import RerankClient
from memory_system.retrieval import pack_context, render_data_only

_CONTEXT_BUILDS = Counter(
    "memory_context_builds_total",
    "Memory context builds",
    ("purpose", "truncated"),
)
_CONTEXT_ITEMS = Counter(
    "memory_context_items_total",
    "Memory items packed into contexts",
    ("purpose",),
)
_RECALL_OUTCOMES = Counter(
    "memory_recall_outcomes_total",
    "Query recall outcomes after context packing",
    ("purpose", "outcome"),
)
_PROFILE_TOKEN_BUDGET = 512
_EMBEDDING_FALLBACKS = Counter(
    "memory_query_embedding_fallbacks_total", "Query embeddings unavailable during recall"
)
_RERANK_FALLBACKS = Counter(
    "memory_rerank_fallbacks_total", "Reranking unavailable during recall"
)


class MemoryPolicyRejected(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class MemorySystem:
    def __init__(
        self,
        repository: PostgresRepository,
        policy: MemoryPolicy,
        max_context_tokens: int,
        embedding_client: EmbeddingClient | None = None,
        rerank_client: RerankClient | None = None,
    ) -> None:
        self.repository = repository
        self.policy = policy
        self.max_context_tokens = max_context_tokens
        self.embedding_client = embedding_client
        self.rerank_client = rerank_client

    async def record_event(self, event: InteractionEvent) -> RecordReceipt:
        return await self.repository.record_event(event)

    async def build_context(self, request: RecallRequest) -> MemoryContext:
        budget = min(request.token_budget, self.max_context_tokens)
        scope = request_scope(request)
        profile_candidates = await self.repository.profile(scope, request.now)
        profile_items, profile_truncated = pack_context(
            profile_candidates, min(budget, _PROFILE_TOKEN_BUDGET)
        )
        search_args = (scope, request.query, request.purpose, request.now)
        embedder = self.embedding_client
        vector = None
        model_query_allowed = (
            budget > 0 and bool(request.query.strip())
            and self.policy.evaluate_explicit_memory(request.query).allowed
        )
        if embedder is not None and model_query_allowed:
            try:
                vector, = await asyncio.to_thread(embedder.embed, [request.query])
            except (httpx.HTTPError, ValueError):
                _EMBEDDING_FALLBACKS.inc()
        if vector is not None and embedder is not None:
            recalled = await self.repository.search(
                *search_args, embedding_model=embedder.model, query_embedding=vector
            )
        else:
            recalled = await self.repository.search(*search_args)
        matched = [item for item in recalled if item.query_matched]
        if self.rerank_client is not None and model_query_allowed:
            positions = [i for i, item in enumerate(matched) if item.rerank_eligible][:16]
            if len(positions) > 1:
                eligible = [matched[i] for i in positions]
                try:
                    order = await self.rerank_client.rank(
                        request.query, [item.statement for item in eligible]
                    )
                except (httpx.HTTPError, ValueError):
                    _RERANK_FALLBACKS.inc()
                else:
                    for position, index in zip(positions, order, strict=True):
                        matched[position] = eligible[index]
        matched_ids = {item.memory_id for item in matched}
        profile_ids = {item.memory_id for item in profile_items}
        candidates = matched + [
            item for item in profile_items if item.memory_id not in matched_ids
        ] + [
            item for item in recalled if not item.query_matched and item.memory_id not in profile_ids
        ]
        selected, retrieval_truncated = pack_context(candidates, budget)
        truncated = profile_truncated or retrieval_truncated
        diagnostics_id = str(uuid4())
        await self.repository.record_recall_diagnostic(
            scope,
            diagnostics_id,
            request.purpose,
            budget,
            len({item.memory_id for item in profile_candidates + recalled}),
            [item.memory_id for item in selected],
            truncated,
        )
        _CONTEXT_BUILDS.labels(
            purpose=request.purpose, truncated=str(truncated).lower()
        ).inc()
        _CONTEXT_ITEMS.labels(purpose=request.purpose).inc(len(selected))
        if request.query.strip():
            if any(item.query_matched for item in selected):
                outcome = "matched"
            elif any(item.memory_id not in profile_ids for item in selected):
                outcome = "context_only"
            elif selected:
                outcome = "profile_only"
            else:
                outcome = "empty"
            _RECALL_OUTCOMES.labels(purpose=request.purpose, outcome=outcome).inc()
        return MemoryContext(
            items=selected,
            rendered_text=render_data_only(selected),
            truncated=truncated,
            diagnostics_id=diagnostics_id,
        )

    async def build_profile(
        self,
        scope: MemoryScope,
        purpose: str,
        token_budget: int,
        now: datetime,
    ) -> ProfileProjection:
        budget = min(token_budget, self.max_context_tokens, _PROFILE_TOKEN_BUDGET)
        candidates = await self.repository.profile(scope, now)
        selected, truncated = pack_context(candidates, budget)
        diagnostics_id = str(uuid4())
        await self.repository.record_recall_diagnostic(
            scope,
            diagnostics_id,
            purpose,
            budget,
            len(candidates),
            [item.memory_id for item in selected],
            truncated,
        )
        return ProfileProjection(
            items=selected,
            rendered_text=render_data_only(selected),
            truncated=truncated,
            diagnostics_id=diagnostics_id,
            as_of=now,
        )

    async def execute_command_idempotent(
        self, command: MemoryCommand, key: str, payload_hash: str
    ) -> CommandReceipt:
        self._validate_command(command)
        return await self.repository.execute_command_idempotent(
            command, key, payload_hash, self.policy.version
        )

    async def list_memories(self, query: MemoryListQuery) -> Sequence[MemorySummary]:
        return await self.repository.list_memories(query)

    async def list_audit(self, scope: MemoryScope, limit: int) -> list[AuditEntry]:
        return await self.repository.list_audit(scope, limit)

    async def list_candidate_decisions(
        self, scope: MemoryScope, limit: int
    ) -> list[CandidateDecisionAudit]:
        return await self.repository.list_candidate_decisions(scope, limit)

    async def get_recall_diagnostic(
        self, scope: MemoryScope, diagnostics_id: str
    ) -> RecallDiagnostic:
        return await self.repository.get_recall_diagnostic(scope, diagnostics_id)

    async def review_memory(
        self, scope: MemoryScope, memory_id: str, decision: str
    ) -> MemoryReviewReceipt:
        return await self.repository.review_memory(scope, memory_id, decision)

    def _require_allowed(self, statement: str) -> None:
        decision = self.policy.evaluate_explicit_memory(statement)
        if not decision.allowed:
            raise MemoryPolicyRejected(decision.reason or "policy_rejected")

    def _validate_command(self, command: MemoryCommand) -> None:
        if isinstance(command, RememberCommand | RememberProcedureCommand):
            self._require_allowed(command.statement)
        elif isinstance(command, CorrectCommand | CorrectProcedureCommand):
            self._require_allowed(command.replacement)


def request_scope(request: RecallRequest) -> MemoryScope:
    return MemoryScope(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        agent_id=request.agent_id,
    )

