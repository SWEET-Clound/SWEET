from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field


def _bounded_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > 32_768:
        raise ValueError("metadata exceeds 32 KiB")
    return value


BoundedMetadata = Annotated[dict[str, Any], AfterValidator(_bounded_metadata)]


class EventSource(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    WEB = "web"
    ADMIN = "admin"


class MemoryLayer(StrEnum):
    WORKING = "L1"
    EPISODIC = "L2"
    SEMANTIC = "L3"
    PROCEDURAL = "L4"


class Identity(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    allowed_purposes: set[str] = Field(default_factory=set)
    memory_write: bool = False

    def scope(self) -> MemoryScope:
        return MemoryScope(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
        )


class MemoryScope(BaseModel):
    tenant_id: str
    user_id: str
    agent_id: str


class InteractionEvent(BaseModel):
    tenant_id: str
    user_id: str
    agent_id: str
    thread_id: str
    idempotency_key: str
    source: EventSource
    content: str = Field(min_length=1, max_length=100_000)
    occurred_at: datetime
    metadata: BoundedMetadata = Field(default_factory=dict)
    allow_memory_extraction: bool = False


class RecallRequest(BaseModel):
    tenant_id: str
    user_id: str
    agent_id: str
    thread_id: str
    query: str = Field(min_length=1, max_length=10_000)
    purpose: Literal["conversation", "task", "history", "support"]
    token_budget: int = Field(ge=0, le=4096)
    now: datetime


class MemoryContextItem(BaseModel):
    memory_id: str
    statement: str
    layer: MemoryLayer
    valid_from: datetime | None
    valid_to: datetime | None
    confidence: float
    source_event_ids: list[str]
    conflict_memory_ids: list[str] = Field(default_factory=list)
    query_matched: bool = Field(default=False, exclude=True)


class MemoryContext(BaseModel):
    items: list[MemoryContextItem]
    rendered_text: str
    truncated: bool
    diagnostics_id: str


class ProfileProjection(BaseModel):
    items: list[MemoryContextItem]
    rendered_text: str
    truncated: bool
    diagnostics_id: str
    as_of: datetime


class RecallDiagnostic(BaseModel):
    diagnostics_id: str
    purpose: Literal["conversation", "task", "history", "support"]
    token_budget: int
    candidate_count: int
    selected_memory_ids: list[str]
    truncated: bool
    created_at: datetime


class RecordReceipt(BaseModel):
    event_id: str
    recorded: bool
    duplicate: bool
    extraction_status: str | None = None


class EventProcessingStatus(BaseModel):
    event_id: str
    extraction_status: str
    attempts: int
    error_code: str | None
    updated_at: datetime


class CommandReceipt(BaseModel):
    command_id: str
    status: Literal["completed", "deletion_pending"]
    affected_memory_ids: list[str]
    deletion_job_id: str | None = None


class MemoryListQuery(BaseModel):
    scope: MemoryScope
    layers: list[MemoryLayer] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=lambda: ["active"])
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=101)


class MemorySummary(BaseModel):
    memory_id: str
    statement: str
    layer: MemoryLayer
    status: str
    valid_from: datetime | None
    valid_to: datetime | None
    expires_at: datetime | None
    source_event_ids: list[str]
    kind: str | None = None
    confidence: float | None = None
    future_utility: str | None = None
    affect: AffectSignal | None = None
    policy_version: str | None = None
    subject_key: str | None = None
    sensitivity: Literal["low", "medium", "high", "secret"] = "low"
    poisoning_flags: list[str] = Field(default_factory=list)
    conflict_memory_ids: list[str] = Field(default_factory=list)


class AffectSignal(BaseModel):
    valence: float = Field(ge=-1, le=1)
    arousal: float = Field(ge=0, le=1)
    labels: list[str]
    confidence: float = Field(ge=0, le=1)
    observed_at: datetime
    derived_from: Literal["text", "voice", "multimodal"]


class MemoryCandidate(BaseModel):
    statement: str = Field(min_length=1, max_length=10_000)
    proposed_layer: MemoryLayer
    kind: str
    subject_key: str | None
    valid_from: datetime | None
    valid_to: datetime | None
    expires_at: datetime | None = None
    source_event_ids: list[str]
    confidence: float = Field(ge=0, le=1)
    future_utility: Literal["low", "medium", "high"]
    durability: Literal["transient", "uncertain", "stable"]
    affect: AffectSignal | None
    sensitivity: Literal["low", "medium", "high", "secret"]
    poisoning_flags: list[str]


class CandidateDecision(BaseModel):
    candidate: MemoryCandidate
    outcome: Literal["stored", "quarantined", "rejected"]
    reason: str


class CandidateDecisionAudit(BaseModel):
    decision_id: str
    event_id: str
    candidate_ordinal: int
    kind: str
    proposed_layer: MemoryLayer
    subject_key: str | None
    sensitivity: Literal["low", "medium", "high", "secret"]
    poisoning_flags: list[str]
    outcome: Literal["stored", "quarantined", "rejected"]
    reason: str
    policy_version: str
    stored_memory_id: str | None
    created_at: datetime


class RememberCommand(BaseModel):
    action: Literal["remember"]
    scope: MemoryScope
    statement: str = Field(min_length=1, max_length=10_000)
    source_event_id: str


class RememberProcedureCommand(BaseModel):
    action: Literal["remember_procedure"]
    scope: MemoryScope
    statement: str = Field(min_length=1, max_length=10_000)
    source_event_id: str


class CorrectCommand(BaseModel):
    action: Literal["correct"]
    scope: MemoryScope
    memory_id: str
    replacement: str = Field(min_length=1, max_length=10_000)
    source_event_id: str


class CorrectProcedureCommand(BaseModel):
    action: Literal["correct_procedure"]
    scope: MemoryScope
    memory_id: str
    replacement: str = Field(min_length=1, max_length=10_000)
    source_event_id: str


class ForgetCommand(BaseModel):
    action: Literal["forget"]
    scope: MemoryScope
    memory_ids: list[str] = Field(min_length=1, max_length=100)


class DoNotRememberCommand(BaseModel):
    action: Literal["do_not_remember"]
    scope: MemoryScope
    event_ids: list[str] = Field(min_length=1, max_length=100)


MemoryCommand = Annotated[
    RememberCommand
    | RememberProcedureCommand
    | CorrectCommand
    | CorrectProcedureCommand
    | ForgetCommand
    | DoNotRememberCommand,
    Field(discriminator="action"),
]


class EventRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    source: EventSource
    content: str = Field(min_length=1, max_length=100_000)
    occurred_at: datetime
    metadata: BoundedMetadata = Field(default_factory=dict)

    def to_domain(self, identity: Identity, idempotency_key: str) -> InteractionEvent:
        return InteractionEvent(
            **identity.scope().model_dump(),
            thread_id=self.thread_id,
            idempotency_key=idempotency_key,
            source=self.source,
            content=self.content,
            occurred_at=self.occurred_at,
            metadata=self.metadata,
            allow_memory_extraction=identity.memory_write,
        )


class RecallRequestBody(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=10_000)
    purpose: Literal["conversation", "task", "history", "support"]
    token_budget: int = Field(ge=0, le=4096)
    request_time: datetime


class RememberRequest(BaseModel):
    action: Literal["remember"]
    statement: str = Field(min_length=1, max_length=10_000)
    source_event_id: str


class RememberProcedureRequest(BaseModel):
    action: Literal["remember_procedure"]
    statement: str = Field(min_length=1, max_length=10_000)
    source_event_id: str


class CorrectRequest(BaseModel):
    action: Literal["correct"]
    memory_id: str
    replacement: str = Field(min_length=1, max_length=10_000)
    source_event_id: str


class CorrectProcedureRequest(BaseModel):
    action: Literal["correct_procedure"]
    memory_id: str
    replacement: str = Field(min_length=1, max_length=10_000)
    source_event_id: str


class ForgetRequest(BaseModel):
    action: Literal["forget"]
    memory_ids: list[str] = Field(min_length=1, max_length=100)


class DoNotRememberRequest(BaseModel):
    action: Literal["do_not_remember"]
    event_ids: list[str] = Field(min_length=1, max_length=100)


CommandRequest = Annotated[
    RememberRequest
    | RememberProcedureRequest
    | CorrectRequest
    | CorrectProcedureRequest
    | ForgetRequest
    | DoNotRememberRequest,
    Field(discriminator="action"),
]


class DeletionStatus(BaseModel):
    job_id: str
    status: str
    attempts: int
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class AuditEntry(BaseModel):
    audit_id: str
    action: str
    target_id: str
    created_at: datetime


class MemoryReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]


class MemoryReviewReceipt(BaseModel):
    memory_id: str
    status: Literal["active", "rejected"]


class EventJob(BaseModel):
    job_id: int
    event_id: str
    event: InteractionEvent
    attempts: int

