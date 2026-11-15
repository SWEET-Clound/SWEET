import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID, uuid4

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from memory_system.consolidation import HeuristicExtractor
from memory_system.llm import OpenAICompatibleExtractor
from memory_system.models import (
    EventSource,
    InteractionEvent,
    MemoryCandidate,
    MemoryLayer,
    MemoryScope,
)
from memory_system.policy import MemoryPolicy, explicitly_opts_out
from memory_system.system import ConsolidationUnavailable, MemorySystem


class ReadableText(Protocol):
    def read_text(self, encoding: str = "utf-8") -> str: ...


class Extractor(Protocol):
    def extract(self, event: InteractionEvent) -> list[MemoryCandidate]: ...


class GoldCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    occurred_at: AwareDatetime = datetime(2026, 11, 4, tzinfo=UTC)
    source: EventSource = EventSource.USER
    expected_layers: list[MemoryLayer]
    required_fragments: dict[MemoryLayer, list[str]] = Field(default_factory=dict)
    required_time_alternatives: dict[MemoryLayer, list[str]] = Field(default_factory=dict)
    max_ttl_hours: dict[MemoryLayer, int] = Field(default_factory=dict)
    min_ttl_hours: dict[MemoryLayer, int] = Field(default_factory=dict)
    min_arousal: float | None = Field(default=None, ge=0, le=1)
    expected_utility: dict[MemoryLayer, Literal["low", "medium", "high"]] = Field(
        default_factory=dict
    )
    expected_reasons: list[str] = Field(default_factory=list)


def default_gold_path() -> ReadableText:
    return files("memory_system").joinpath("evals/chinese_memory_gold.jsonl")


def evaluate_file(
    path: ReadableText, extractor: Extractor | None = None, *, check_reasons: bool = True,
    working_memory_ttl_hours: int = 72,
) -> tuple[int, list[str]]:
    if extractor is None:
        extractor = HeuristicExtractor(working_memory_ttl_hours)
    policy = MemoryPolicy(working_memory_ttl_hours)
    failures: list[str] = []
    cases: dict[str, GoldCase] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = GoldCase.model_validate_json(line)
        except ValidationError:
            failures.append(f"line {number}: invalid gold case")
            continue
        if case.id in cases:
            failures.append(f"line {number}: duplicate case id")
        else:
            cases[case.id] = case
    if failures or not cases:
        return 0, failures or ["gold set is empty"]

    for case in cases.values():
        event = InteractionEvent(
            tenant_id="eval-tenant",
            user_id="eval-user",
            agent_id="eval-agent",
            thread_id="eval-thread",
            idempotency_key=str(uuid4()),
            source=case.source,
            content=case.content,
            occurred_at=case.occurred_at,
        )
        accepted: list[str] = []
        accepted_candidates: list[MemoryCandidate] = []
        rejected: list[str] = []
        try:
            candidates = [] if explicitly_opts_out(event) else extractor.extract(event)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            failures.append(f"{case.id}: extraction_error ({type(exc).__name__})")
            continue
        for candidate in candidates:
            decision = policy.evaluate_candidate(candidate, event)
            if decision.allowed:
                accepted.append(candidate.proposed_layer.value)
                accepted_candidates.append(candidate)
            elif decision.reason:
                rejected.append(decision.reason)
        expected_layers = sorted(layer.value for layer in case.expected_layers)
        expected_reasons = sorted(case.expected_reasons)
        missing_content = [
            layer.value
            for layer, fragments in case.required_fragments.items()
            if not any(
                candidate.proposed_layer == layer
                and all(fragment in candidate.statement for fragment in fragments)
                for candidate in accepted_candidates
            )
        ]
        if missing_content:
            failures.append(f"{case.id}: required content missing in {sorted(missing_content)!r}")
        for layer, alternatives in case.required_time_alternatives.items():
            if not any(
                candidate.proposed_layer == layer
                and all(fragment in candidate.statement for fragment in case.required_fragments.get(layer, []))
                and any(value in candidate.statement for value in alternatives)
                for candidate in accepted_candidates
            ):
                failures.append(f"{case.id}: expected time wording missing in {layer.value}")
        if case.min_arousal is not None and not any(
            candidate.proposed_layer is MemoryLayer.WORKING
            and candidate.affect is not None
            and candidate.affect.arousal >= case.min_arousal
            for candidate in accepted_candidates
        ):
            failures.append(f"{case.id}: required working-memory affect missing or too weak")
        for candidate in accepted_candidates:
            layer = candidate.proposed_layer
            expected_utility = case.expected_utility.get(layer)
            if expected_utility is not None and candidate.future_utility != expected_utility:
                failures.append(f"{case.id}: unexpected future utility for {layer.value}")
            max_hours = case.max_ttl_hours.get(layer)
            min_hours = case.min_ttl_hours.get(layer)
            if min_hours is not None and (
                candidate.expires_at is None
                or candidate.expires_at < event.occurred_at + timedelta(hours=min_hours)
            ):
                failures.append(f"{case.id}: expiry too early for {layer.value}")
            if max_hours is not None and (
                candidate.expires_at is None
                or not event.occurred_at < candidate.expires_at
                <= event.occurred_at + timedelta(hours=max_hours)
            ):
                failures.append(f"{case.id}: expiry outside expected window for {layer.value}")
        if sorted(accepted) != expected_layers or (
            check_reasons and sorted(set(rejected)) != expected_reasons
        ):
            failures.append(
                f"{case.id}: layers={sorted(accepted)!r}, reasons={sorted(set(rejected))!r}"
            )
    return len(cases), failures


class ConsolidationGoldEvent(BaseModel, extra="forbid"):
    content: str = Field(min_length=1)
    occurred_at: AwareDatetime


class ConsolidationGoldCase(BaseModel, extra="forbid"):
    id: str
    kind: Literal["episode", "procedure"]
    events: list[ConsolidationGoldEvent] = Field(min_length=2, max_length=8)
    expected_sources: list[int] = Field(default_factory=list)
    required_alternatives: list[list[str]] = Field(default_factory=list)


class ConsolidationGoldRepository:
    def __init__(self, events: dict[str, InteractionEvent]) -> None:
        self.events = events

    async def consolidation_events(self, scope: MemoryScope, event_ids: list[str]):
        return sorted(
            ((event_id, self.events[event_id]) for event_id in event_ids),
            key=lambda pair: (pair[1].occurred_at, pair[0]),
        )

    async def consume_consolidation_quota(self, scope: MemoryScope, limit: int) -> None:
        return None


async def evaluate_consolidation_file(
    path: ReadableText, extractor: OpenAICompatibleExtractor,
) -> tuple[int, list[str]]:
    cases = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return 0, ["could not read consolidation gold set"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            case = ConsolidationGoldCase.model_validate_json(line)
        except ValidationError:
            return 0, [f"line {line_number}: invalid consolidation gold case"]
        indexes = set(case.expected_sources)
        if (
            indexes and (len(indexes) < 2 or not indexes <= set(range(1, len(case.events) + 1)))
            or any(not group or any(not part for part in group) for group in case.required_alternatives)
        ):
            return 0, [f"line {line_number}: invalid expected evidence or text"]
        cases.append(case)
    if not cases:
        return 0, ["consolidation gold set is empty"]
    failures = []
    scope = MemoryScope(tenant_id="gold", user_id="gold", agent_id="gold")
    for case in cases:
        events = {
            str(UUID(int=index)): InteractionEvent(
                **scope.model_dump(), **sample.model_dump(), thread_id="gold",
                idempotency_key=f"{case.id}-{index}", source=EventSource.USER,
            )
            for index, sample in enumerate(case.events, start=1)
        }
        system = MemorySystem(
            ConsolidationGoldRepository(events), MemoryPolicy(), 2048,
            consolidation_extractor=extractor,
        )
        try:
            drafts = await system.propose_consolidation(
                scope, list(events), kind=case.kind, automatic_sources=True,
            )
        except ConsolidationUnavailable:
            failures.append(f"{case.id}: model unavailable")
            continue
        expected = {str(UUID(int=index)) for index in case.expected_sources}
        if not expected:
            if drafts:
                failures.append(f"{case.id}: expected no draft")
        elif not drafts or not all(
            set(draft.source_event_ids) == expected
            and all(any(part in draft.statement for part in group) for group in case.required_alternatives)
            for draft in drafts
        ):
            failures.append(f"{case.id}: missing draft or a draft had unexpected evidence or content")
    return len(cases), failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Chinese memory gold set")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--configured-extractor",
        action="store_true",
        help="Use worker settings; eligible sample text may be sent to the configured model",
    )
    mode.add_argument(
        "--configured-consolidation", action="store_true",
        help="Send synthetic multi-event cases to the configured consolidation model",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
    )
    args = parser.parse_args()
    path = args.path or default_gold_path()
    if args.configured_consolidation:
        from memory_system.llm import build_consolidation_extractor
        from memory_system.settings import Settings

        extractor = build_consolidation_extractor(Settings())  # type: ignore[call-arg]
        if extractor is None:
            parser.error("MEMORY_CONSOLIDATION_MODEL and MEMORY_MODEL_BASE_URL are required")
        try:
            total, failures = asyncio.run(evaluate_consolidation_file(
                args.path or files("memory_system").joinpath("evals/chinese_consolidation_gold.jsonl"),
                extractor,
            ))
        finally:
            extractor.close()
    elif args.configured_extractor:
        from memory_system.settings import Settings
        from memory_system.worker import build_extractor

        settings = Settings()  # type: ignore[call-arg]
        extractor = build_extractor(settings)
        try:
            total, failures = evaluate_file(
                path, extractor, check_reasons=settings.extraction_mode == "heuristic",
                working_memory_ttl_hours=settings.working_memory_ttl_hours,
            )
        finally:
            close = getattr(extractor, "close", None)
            if close is not None:
                close()
    else:
        total, failures = evaluate_file(path)
    print(json.dumps({"cases": total, "failures": failures}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
