"""Typed HTTP client for the core memory lifecycle."""

from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Literal
from uuid import UUID

import httpx

from memory_system.models import (
    AffectObservation,
    AuditEntry,
    BackgroundConsolidationConfirmation,
    BackgroundConsolidationConsent,
    BackgroundConsolidationDraftPage,
    CandidateDecisionAudit,
    CommandReceipt,
    CommandRequest,
    ConsolidationRequest,
    DeletionStatus,
    EventRequest,
    MemoryCandidate,
    MemoryContext,
    MemoryLayer,
    MemoryReviewReceipt,
    MemoryReviewRequest,
    MemorySummary,
    RecallDiagnostic,
    RecallRequestBody,
    RecordReceipt,
    RememberProcedureRequest,
    RememberRequest,
    ThreadConsolidationRequest,
)

_BACKGROUND_CONSOLIDATION = "/v1/memory/consolidations/background"


class MemoryClient:
    """Use a caller-owned HTTP client configured with a base URL and credentials."""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http

    def record_event(self, event: EventRequest, idempotency_key: str) -> RecordReceipt:
        response = self.http.post(
            "/v1/memory/events",
            json=event.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key},
        )
        return RecordReceipt.model_validate(response.raise_for_status().json())

    def import_events(
        self, events: Iterable[tuple[EventRequest, str]]
    ) -> Iterator[RecordReceipt]:
        """Replay events individually with caller-supplied stable idempotency keys."""
        for event, key in events:
            yield self.record_event(event, key)

    def iter_memories(
        self,
        *,
        page_size: int = 100,
        layers: Iterable[MemoryLayer] = (),
        statuses: Iterable[str] = (),
    ) -> Iterator[MemorySummary]:
        """Stream visible memory summaries, defaulting to active memories."""
        params: dict[str, str | int | list[str]] = {
            "limit": page_size,
            "layers": [layer.value for layer in layers],
            "statuses": list(statuses),
        }
        seen: set[str] = set()
        while True:
            response = self.http.get("/v1/memory/items", params=params).raise_for_status()
            for item in response.json():
                yield MemorySummary.model_validate(item)
            cursor = response.headers.get("X-Next-Cursor")
            if not cursor:
                return
            if cursor in seen:
                raise RuntimeError("memory pagination repeated a cursor")
            seen.add(cursor)
            params["cursor"] = cursor

    def build_context(self, request: RecallRequestBody) -> MemoryContext:
        response = self.http.post("/v1/memory/context:build", json=request.model_dump(mode="json"))
        return MemoryContext.model_validate(response.raise_for_status().json())

    def discover_consolidation_threads(self) -> list[str]:
        response = self.http.get("/v1/memory/consolidations/threads")
        return response.raise_for_status().json()

    def set_background_consolidation(self, enabled: bool) -> None:
        request = BackgroundConsolidationConsent(enabled=enabled)
        self.http.put(
            _BACKGROUND_CONSOLIDATION, json=request.model_dump(mode="json")
        ).raise_for_status()

    def list_background_consolidation_drafts(
        self, *, cursor: str | None = None, limit: int = 20,
    ) -> BackgroundConsolidationDraftPage:
        params: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        response = self.http.get(_BACKGROUND_CONSOLIDATION, params=params)
        return BackgroundConsolidationDraftPage.model_validate(response.raise_for_status().json())

    def dismiss_background_consolidation_drafts(self, source_digest: str) -> None:
        self.http.delete(f"{_BACKGROUND_CONSOLIDATION}/{source_digest}").raise_for_status()

    def confirm_background_consolidation(
        self, source_digest: str, confirmation: BackgroundConsolidationConfirmation,
        idempotency_key: str,
    ) -> CommandReceipt:
        response = self.http.post(
            f"{_BACKGROUND_CONSOLIDATION}/{source_digest}/confirm",
            json=confirmation.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key},
        )
        return CommandReceipt.model_validate(response.raise_for_status().json())

    def discover_consolidation_sources(self, thread_id: str) -> list[str]:
        response = self.http.get(
            "/v1/memory/consolidations/sources", params={"thread_id": thread_id}
        )
        return [str(UUID(event_id)) for event_id in response.raise_for_status().json()]

    def preview_consolidation(
        self, event_ids: list[str] | None = None, *,
        thread_id: str | None = None, kind: Literal["episode", "procedure"] = "episode",
    ) -> list[MemoryCandidate]:
        if event_ids is not None and thread_id is None:
            request = ConsolidationRequest(event_ids=event_ids, kind=kind)
        elif thread_id is not None and event_ids is None:
            request = ThreadConsolidationRequest(thread_id=thread_id, kind=kind)
        else:
            raise ValueError("provide exactly one of event_ids or thread_id")
        response = self.http.post(
            "/v1/memory/consolidations:preview",
            json=request.model_dump(mode="json", exclude_defaults=True),
        )
        return [MemoryCandidate.model_validate(item) for item in response.raise_for_status().json()]

    def execute_command(self, command: CommandRequest, idempotency_key: str) -> CommandReceipt:
        response = self.http.post(
            "/v1/memory/commands",
            json=command.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key},
        )
        return CommandReceipt.model_validate(response.raise_for_status().json())

    def confirm_consolidation(
        self, proposal: MemoryCandidate, confirmation_event_id: str, idempotency_key: str
    ) -> CommandReceipt:
        source_ids = {UUID(event_id) for event_id in proposal.source_event_ids}
        if not 2 <= len(source_ids) <= 8:
            raise ValueError("confirmation requires two to eight distinct supporting events")
        if UUID(confirmation_event_id) in source_ids:
            raise ValueError("confirmation event must be distinct from supporting events")
        if proposal.proposed_layer is MemoryLayer.PROCEDURAL and proposal.expires_at is None:
            return self.execute_command(
                RememberProcedureRequest(
                    action="remember_procedure", statement=proposal.statement,
                    source_event_id=confirmation_event_id,
                    supporting_event_ids=proposal.source_event_ids,
                ),
                idempotency_key,
            )
        if proposal.proposed_layer is not MemoryLayer.EPISODIC or proposal.expires_at is None:
            raise ValueError("confirmation requires an expiring L2 or non-expiring L4 proposal")
        return self.execute_command(
            RememberRequest(
                action="remember", statement=proposal.statement,
                source_event_id=confirmation_event_id,
                supporting_event_ids=proposal.source_event_ids, expires_at=proposal.expires_at,
            ),
            idempotency_key,
        )

    def review_memory(
        self, memory_id: str, decision: Literal["approve", "reject"]
    ) -> MemoryReviewReceipt:
        request = MemoryReviewRequest(decision=decision)
        response = self.http.post(
            f"/v1/memory/items/{UUID(memory_id)}:review", json=request.model_dump(mode="json")
        )
        return MemoryReviewReceipt.model_validate(response.raise_for_status().json())

    def list_candidate_decisions(self, *, limit: int = 50) -> list[CandidateDecisionAudit]:
        response = self.http.get("/v1/memory/candidate-decisions", params={"limit": limit})
        return [
            CandidateDecisionAudit.model_validate(item)
            for item in response.raise_for_status().json()
        ]

    def resolve_memory_conflict_as_coexisting(self, memory_id: str, other_id: str) -> None:
        self.http.put(
            f"/v1/memory/items/{UUID(memory_id)}/conflicts/{UUID(other_id)}/coexistence"
        ).raise_for_status()

    def list_audit(self, *, limit: int = 50) -> list[AuditEntry]:
        response = self.http.get("/v1/memory/audit", params={"limit": limit})
        return [AuditEntry.model_validate(item) for item in response.raise_for_status().json()]

    def get_recall_diagnostic(self, diagnostics_id: str) -> RecallDiagnostic:
        response = self.http.get(f"/v1/memory/diagnostics/{UUID(diagnostics_id)}")
        return RecallDiagnostic.model_validate(response.raise_for_status().json())

    def deletion_status(self, job_id: str) -> DeletionStatus:
        response = self.http.get(f"/v1/memory/deletions/{job_id}")
        return DeletionStatus.model_validate(response.raise_for_status().json())

    def affect_history(
        self, memory_id: str, *, limit: int = 50, as_of: datetime | None = None
    ) -> list[AffectObservation]:
        params: dict[str, str | int] = {"limit": limit}
        if as_of is not None:
            params["as_of"] = as_of.isoformat()
        response = self.http.get(
            f"/v1/memory/items/{memory_id}/affect-history", params=params
        )
        return [AffectObservation.model_validate(item) for item in response.raise_for_status().json()]
