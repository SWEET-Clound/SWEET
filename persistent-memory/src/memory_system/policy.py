import re
from dataclasses import dataclass
from datetime import timedelta

from memory_system.models import EventSource, InteractionEvent, MemoryCandidate, MemoryLayer

_OPT_OUT = re.compile(r"^\s*(?:请|麻烦)?\s*(?:你)?\s*(?:不要|别)\s*(?:记住|记录|保存)")
REMEMBER_PREFIX = re.compile(r"^\s*(?:请|麻烦)?\s*(?:你)?\s*记住\s*[：:]?\s*(?P<fact>\S.*)$")
EPISODIC_TTL = timedelta(days=180)


def explicitly_opts_out(event: InteractionEvent) -> bool:
    return is_memory_opt_out(event.source, event.content)


def is_memory_opt_out(source: str, content: str) -> bool:
    return source == EventSource.USER and bool(_OPT_OUT.match(content))

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?:身份证|信用卡|银行卡|卡号)\s*[：:]?\s*[0-9Xx -]{12,}"),
    re.compile(r"(?:诊断为|患有|确诊)\s*[^，。,.]{1,40}"),
)
_INSTRUCTION_PATTERNS = (
    re.compile(r"忽略(?:之前|以上|系统).*(?:规则|指令|提示)", re.IGNORECASE),
    re.compile(r"(?:system prompt|系统提示词|越过权限|绕过安全)", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class WriteDecision:
    allowed: bool
    reason: str | None = None


class MemoryPolicy:
    version = "2026-11-candidate-metadata-v6"

    def __init__(self, working_memory_ttl_hours: int = 72) -> None:
        self.working_memory_ttl = timedelta(hours=working_memory_ttl_hours)

    def evaluate_explicit_memory(self, statement: str) -> WriteDecision:
        if any(pattern.search(statement) for pattern in _SECRET_PATTERNS):
            return WriteDecision(False, "credentials_and_secrets_are_not_stored")
        if any(pattern.search(statement) for pattern in _INSTRUCTION_PATTERNS):
            return WriteDecision(False, "instruction_like_memory_is_not_stored")
        return WriteDecision(True)

    def evaluate_candidate(
        self, candidate: MemoryCandidate, event: InteractionEvent | None = None
    ) -> WriteDecision:
        if event is not None and event.source is not EventSource.USER:
            return WriteDecision(False, "source_cannot_create_user_memory")
        if candidate.poisoning_flags:
            return WriteDecision(False, "poisoning_signal")
        if candidate.sensitivity in {"high", "secret"}:
            return WriteDecision(False, "sensitive_content_requires_explicit_consent")
        text_fields = [candidate.statement, candidate.kind, candidate.subject_key or ""]
        if candidate.affect is not None:
            text_fields.extend(candidate.affect.labels)
        for value in text_fields:
            content_decision = self.evaluate_explicit_memory(value)
            if not content_decision.allowed:
                return content_decision
        if candidate.valid_from is not None and any(
            end is not None and end <= candidate.valid_from
            for end in (candidate.valid_to, candidate.expires_at)
        ):
            return WriteDecision(False, "memory_validity_window_is_empty")
        if candidate.proposed_layer is MemoryLayer.WORKING and candidate.expires_at is None:
            return WriteDecision(False, "working_memory_requires_expiry")
        if candidate.proposed_layer is MemoryLayer.EPISODIC and candidate.expires_at is None:
            return WriteDecision(False, "episodic_memory_requires_expiry")
        if event is not None and candidate.proposed_layer in {
            MemoryLayer.WORKING, MemoryLayer.EPISODIC
        }:
            working = candidate.proposed_layer is MemoryLayer.WORKING
            ttl = self.working_memory_ttl if working else EPISODIC_TTL
            expiry = candidate.expires_at
            if expiry is not None and (
                expiry.utcoffset() is None
                or not event.occurred_at < expiry <= event.occurred_at + ttl
            ):
                reason = (
                    "working_memory_expiry_outside_policy_window"
                    if working else "episodic_memory_expiry_outside_policy_window"
                )
                return WriteDecision(False, reason)
        if candidate.proposed_layer is MemoryLayer.SEMANTIC and (
            candidate.durability != "stable" or candidate.confidence < 0.85
        ):
            return WriteDecision(False, "semantic_memory_requires_stable_evidence")
        if candidate.proposed_layer is MemoryLayer.SEMANTIC:
            explicit = REMEMBER_PREFIX.match(event.content) if event is not None else None
            if explicit is None:
                return WriteDecision(False, "semantic_memory_requires_explicit_consent")
            fact = " ".join(explicit["fact"].split())
            statement = " ".join(candidate.statement.split())
            if statement != fact:
                return WriteDecision(False, "semantic_memory_requires_verbatim_evidence")
        if candidate.proposed_layer is MemoryLayer.PROCEDURAL:
            return WriteDecision(False, "procedural_memory_requires_verified_outcomes")
        return WriteDecision(True)

