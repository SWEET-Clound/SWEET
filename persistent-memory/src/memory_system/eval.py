import argparse
import json
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from memory_system.consolidation import HeuristicExtractor
from memory_system.models import EventSource, InteractionEvent, MemoryCandidate
from memory_system.policy import MemoryPolicy, explicitly_opts_out


class ReadableText(Protocol):
    def read_text(self, encoding: str = "utf-8") -> str: ...


class Extractor(Protocol):
    def extract(self, event: InteractionEvent) -> list[MemoryCandidate]: ...


def default_gold_path() -> ReadableText:
    return files("memory_system").joinpath("evals/chinese_memory_gold.jsonl")


def evaluate_file(
    path: ReadableText, extractor: Extractor | None = None, *, check_reasons: bool = True
) -> tuple[int, list[str]]:
    if extractor is None:
        extractor = HeuristicExtractor()
    policy = MemoryPolicy()
    failures: list[str] = []
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        total += 1
        event = InteractionEvent(
            tenant_id="eval-tenant",
            user_id="eval-user",
            agent_id="eval-agent",
            thread_id="eval-thread",
            idempotency_key=str(uuid4()),
            source=EventSource(case.get("source", "user")),
            content=case["content"],
            occurred_at=datetime(2026, 11, 4, tzinfo=UTC),
        )
        accepted: list[str] = []
        rejected: list[str] = []
        candidates = [] if explicitly_opts_out(event) else extractor.extract(event)
        for candidate in candidates:
            decision = policy.evaluate_candidate(candidate, event)
            if decision.allowed:
                accepted.append(candidate.proposed_layer.value)
            elif decision.reason:
                rejected.append(decision.reason)
        expected_layers = sorted(case.get("expected_layers", []))
        expected_reasons = sorted(case.get("expected_reasons", []))
        if sorted(accepted) != expected_layers or (
            check_reasons and sorted(set(rejected)) != expected_reasons
        ):
            failures.append(
                f"{case['id']}: layers={sorted(accepted)!r}, reasons={sorted(set(rejected))!r}"
            )
    return total, failures


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
                path, extractor, check_reasons=settings.extraction_mode == "heuristic"
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
