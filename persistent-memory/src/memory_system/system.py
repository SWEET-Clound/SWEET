from collections.abc import Sequence
from uuid import uuid4

from memory_system.models import (
    CommandReceipt,
    CorrectCommand,
    InteractionEvent,
    MemoryCommand,
    MemoryContext,
    MemoryListQuery,
    MemoryScope,
    MemorySummary,
    RecallRequest,
    RecordReceipt,
    RememberCommand,
)
from memory_system.policy import MemoryPolicy
from memory_system.postgres import PostgresRepository
from memory_system.retrieval import pack_context, render_data_only


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
    ) -> None:
        self.repository = repository
        self.policy = policy
        self.max_context_tokens = max_context_tokens

    async def record_event(self, event: InteractionEvent) -> RecordReceipt:
        return await self.repository.record_event(event)

    async def build_context(self, request: RecallRequest) -> MemoryContext:
        budget = min(request.token_budget, self.max_context_tokens)
        candidates = await self.repository.search(
            request_scope(request), request.query, request.purpose, request.now
        )
        selected, truncated = pack_context(candidates, budget)
        return MemoryContext(
            items=selected,
            rendered_text=render_data_only(selected),
            truncated=truncated,
            diagnostics_id=str(uuid4()),
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

    def _require_allowed(self, statement: str) -> None:
        decision = self.policy.evaluate_explicit_memory(statement)
        if not decision.allowed:
            raise MemoryPolicyRejected(decision.reason or "policy_rejected")

    def _validate_command(self, command: MemoryCommand) -> None:
        if isinstance(command, RememberCommand):
            self._require_allowed(command.statement)
        elif isinstance(command, CorrectCommand):
            self._require_allowed(command.replacement)


def request_scope(request: RecallRequest) -> MemoryScope:
    return MemoryScope(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        agent_id=request.agent_id,
    )

