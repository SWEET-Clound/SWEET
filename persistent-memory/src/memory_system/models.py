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


class MemoryContext(BaseModel):
    items: list[MemoryContextItem]
    rendered_text: str
    truncated: bool
    diagnostics_id: str


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
    status: Literal["completed", "needs_confirmation", "deletion_pending"]
    affected_memory_ids: list[str]
    deletion_job_id: str | None = None


class MemoryListQuery(BaseModel):
    scope: MemoryScope
    layers: list[MemoryLayer] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=lambda: ["active"])
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=100)


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
    sensitivity: str
    poisoning_flags: list[str]


class RememberCommand(BaseModel):
    action: Literal["remember"]
    scope: MemoryScope
    statement: str = Field(min_length=1, max_length=10_000)
    source_event_id: str


class CorrectCommand(BaseModel):
    action: Literal["correct"]
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
    RememberCommand | CorrectCommand | ForgetCommand | DoNotRememberCommand,
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


class CorrectRequest(BaseModel):
    action: Literal["correct"]
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
    RememberRequest | CorrectRequest | ForgetRequest | DoNotRememberRequest,
    Field(discriminator="action"),
]


class DeletionStatus(BaseModel):
    job_id: str
    status: str
    attempts: int
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class EventJob(BaseModel):
    job_id: int
    event_id: str
    event: InteractionEvent
    attempts: int

