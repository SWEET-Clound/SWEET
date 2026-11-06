import argparse
import json
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from memory_system.consolidation import HeuristicExtractor
from memory_system.models import EventSource, InteractionEvent, MemoryCandidate, MemoryLayer
from memory_system.policy import MemoryPolicy, explicitly_opts_out


class ReadableText(Protocol):
    def read_text(self, encoding: str = "utf-8") -> str: ...


class Extractor(Protocol):
    def extract(self, event: InteractionEvent) -> list[MemoryCandidate]: ...


class GoldCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source: EventSource = EventSource.USER
    expected_layers: list[MemoryLayer]
    required_fragments: dict[MemoryLayer, list[str]] = Field(default_factory=dict)
    max_ttl_hours: dict[MemoryLayer, int] = Field(default_factory=dict)
    expected_reasons: list[str] = Field(default_factory=list)


def default_gold_path() -> ReadableText:
    return files("memory_system").joinpath("evals/chinese_memory_gold.jsonl")


def evaluate_file(
    path: ReadableText, extractor: Extractor | None = None, *, check_reasons: bool = True,
    working_memory_ttl_hours: int = 72,
) -> tuple[int, list[str]]:
    if extractor is None:
        extractor = HeuristicExtractor()
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
            occurred_at=datetime(2026, 11, 4, tzinfo=UTC),
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
        for candidate in accepted_candidates:
            layer = candidate.proposed_layer
            max_hours = case.max_ttl_hours.get(layer)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Chinese memory gold set")
    parser.add_argument(
        "--configured-extractor",
        action="store_true",
        help="Use worker settings; eligible sample text may be sent to the configured model",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
    )
    args = parser.parse_args()
    path = args.path or default_gold_path()
    if args.configured_extractor:
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
