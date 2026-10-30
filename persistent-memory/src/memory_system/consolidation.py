import asyncio
import re
from datetime import datetime, timedelta
from typing import Literal

from memory_system.models import (
    AffectSignal,
    EventJob,
    EventSource,
    InteractionEvent,
    MemoryCandidate,
    MemoryLayer,
)
from memory_system.policy import MemoryPolicy

_REMEMBER_PREFIX = re.compile(r"^\s*(?:请|麻烦)?(?:你)?记住\s*[：:]?\s*(?P<fact>.+)$")
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


class HeuristicExtractor:
    """Offline-safe baseline; optional model extraction can replace it later."""

    def extract(self, event: InteractionEvent) -> list[MemoryCandidate]:
        if event.source is not EventSource.USER:
            return []

        explicit = _REMEMBER_PREFIX.match(event.content)
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
                    expires_at=event.occurred_at + timedelta(hours=24),
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
        extracted = await asyncio.to_thread(self.extractor.extract, job.event)
        candidates = [
            candidate
            for candidate in extracted
            if self.policy.evaluate_candidate(candidate, job.event).allowed
        ]
        if not candidates:
            return []
        return await repository.store_candidates(
            job.event_id,
            job.event,
            candidates,
            self.policy.version,
            self.write_status,
        )


class HybridExtractor:
    def __init__(self, *extractors) -> None:
        self.extractors = extractors

    def extract(self, event: InteractionEvent) -> list[MemoryCandidate]:
        unique: dict[tuple[str, str], MemoryCandidate] = {}
        for extractor in self.extractors:
            for candidate in extractor.extract(event):
                key = (candidate.kind, " ".join(candidate.statement.lower().split()))
                current = unique.get(key)
                if current is None or candidate.confidence > current.confidence:
                    unique[key] = candidate
        return list(unique.values())

