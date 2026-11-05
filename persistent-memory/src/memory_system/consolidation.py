import asyncio
import logging
from datetime import datetime, timedelta
from typing import Literal

from prometheus_client import Counter

from memory_system.models import (
    AffectSignal,
    CandidateDecision,
    EventJob,
    EventSource,
    InteractionEvent,
    MemoryCandidate,
    MemoryLayer,
)
from memory_system.policy import REMEMBER_PREFIX, MemoryPolicy, explicitly_opts_out

_NEGATIVE_HIGH_AROUSAL = (
    "特别难过",
    "非常难过",
    "压力特别大",
    "压力非常大",
    "非常焦虑",
    "特别焦虑",
    "崩溃",
    "terrified",
    "overwhelmed",
    "extremely anxious",
)
_BOUNDED_EVENT_MARKERS = ("会议", "预约", "截止", "提醒我", "计划", "复诊", "面试")
_TIME_MARKERS = ("明天", "后天", "下周", "下个月", "上午", "下午", "今晚")
_LOG = logging.getLogger(__name__)
_CANDIDATE_DECISIONS = Counter(
    "memory_candidate_decisions_total",
    "Policy decisions for extracted memory candidates",
    ("decision", "reason", "layer"),
)


class HeuristicExtractor:
    """Offline-safe baseline; optional model extraction can replace it later."""

    def __init__(self, working_memory_ttl_hours: int = 72) -> None:
        self.working_memory_ttl_hours = working_memory_ttl_hours

    def extract(self, event: InteractionEvent) -> list[MemoryCandidate]:
        if event.source is not EventSource.USER:
            return []
        if explicitly_opts_out(event):
            return []

        explicit = REMEMBER_PREFIX.match(event.content)
        if explicit:
            return [
                self._candidate(
                    event,
                    statement=explicit.group("fact").strip(),
                    layer=MemoryLayer.SEMANTIC,
                    kind="explicit_preference",
                    utility="high",
                    durability="stable",
                    confidence=1.0,
                )
            ]

        candidates: list[MemoryCandidate] = []
        if any(marker in event.content.lower() for marker in _NEGATIVE_HIGH_AROUSAL):
            affect = AffectSignal(
                valence=-0.8,
                arousal=0.85,
                labels=["distress"],
                confidence=0.75,
                observed_at=event.occurred_at,
                derived_from="text",
            )
            candidates.append(
                self._candidate(
                    event,
                    statement=event.content.strip(),
                    layer=MemoryLayer.WORKING,
                    kind="current_affect",
                    utility="medium",
                    durability="transient",
                    confidence=affect.confidence,
                    affect=affect,
                    expires_at=event.occurred_at
                    + timedelta(hours=self.working_memory_ttl_hours),
                )
            )
        if any(marker in event.content for marker in _BOUNDED_EVENT_MARKERS) and any(
            marker in event.content for marker in _TIME_MARKERS
        ):
            candidates.append(
                self._candidate(
                    event,
                    statement=event.content.strip(),
                    layer=MemoryLayer.EPISODIC,
                    kind="bounded_commitment",
                    utility="high",
                    durability="uncertain",
                    confidence=0.85,
                    expires_at=event.occurred_at + timedelta(days=180),
                )
            )
        return candidates

    @staticmethod
    def _candidate(
        event: InteractionEvent,
        *,
        statement: str,
        layer: MemoryLayer,
        kind: str,
        utility: Literal["low", "medium", "high"],
        durability: Literal["transient", "uncertain", "stable"],
        confidence: float,
        affect: AffectSignal | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryCandidate:
        return MemoryCandidate(
            statement=statement,
            proposed_layer=layer,
            kind=kind,
            subject_key=None,
            valid_from=event.occurred_at,
            valid_to=None,
            expires_at=expires_at,
            source_event_ids=[],
            confidence=confidence,
            future_utility=utility,
            durability=durability,
            affect=affect,
            sensitivity="low",
            poisoning_flags=[],
        )


class Consolidator:
    def __init__(
        self,
        extractor: HeuristicExtractor,
        policy: MemoryPolicy,
        write_status: Literal["active", "quarantined"] = "active",
    ) -> None:
        self.extractor = extractor
        self.policy = policy
        self.write_status = write_status

    async def process(self, repository, job: EventJob) -> list[str]:
        if explicitly_opts_out(job.event):
            return []
        extracted = await asyncio.to_thread(self.extractor.extract, job.event)
        candidates: list[MemoryCandidate] = []
        decisions: list[CandidateDecision] = []
        for candidate in extracted:
            decision = self.policy.evaluate_candidate(candidate, job.event)
            _CANDIDATE_DECISIONS.labels(
                decision="allowed" if decision.allowed else "rejected",
                reason=decision.reason or "allowed",
                layer=candidate.proposed_layer.value,
            ).inc()
            if decision.allowed:
                candidates.append(candidate)
                outcome = "quarantined" if self.write_status == "quarantined" else "stored"
                reason = "policy_allowed"
            else:
                outcome = "rejected"
                reason = decision.reason or "policy_rejected"
            decisions.append(
                CandidateDecision(candidate=candidate, outcome=outcome, reason=reason)
            )
        if not decisions:
            return []
        return await repository.store_candidates(
            job.event_id,
            job.event,
            candidates,
            self.policy.version,
            self.write_status,
            decisions,
        )


class HybridExtractor:
    def __init__(self, *extractors) -> None:
        self.extractors = extractors

    def extract(self, event: InteractionEvent) -> list[MemoryCandidate]:
        unique: dict[tuple[str, str], MemoryCandidate] = {}
        succeeded = False
        last_error: Exception | None = None
        for extractor in self.extractors:
            try:
                candidates = extractor.extract(event)
            except Exception as exc:  # noqa: BLE001 - one extractor must not erase another
                last_error = exc
                _LOG.warning(
                    "memory extractor failed",
                    extra={"extractor": type(extractor).__name__, "error": type(exc).__name__},
                )
                continue
            succeeded = True
            for candidate in candidates:
                key = (candidate.kind, " ".join(candidate.statement.lower().split()))
                current = unique.get(key)
                if current is None or candidate.confidence > current.confidence:
                    unique[key] = candidate
        if not succeeded and last_error is not None:
            raise last_error
        return list(unique.values())

    def close(self) -> None:
        for extractor in self.extractors:
            close = getattr(extractor, "close", None)
            if close is not None:
                close()

