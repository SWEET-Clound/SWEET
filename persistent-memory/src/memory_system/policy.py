import re
from dataclasses import dataclass

from memory_system.models import EventSource, InteractionEvent, MemoryCandidate, MemoryLayer

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
    version = "2026-10-first-release-v1"

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
        content_decision = self.evaluate_explicit_memory(candidate.statement)
        if not content_decision.allowed:
            return content_decision
        if candidate.proposed_layer is MemoryLayer.WORKING and candidate.expires_at is None:
            return WriteDecision(False, "working_memory_requires_expiry")
        if candidate.proposed_layer is MemoryLayer.EPISODIC and candidate.expires_at is None:
            return WriteDecision(False, "episodic_memory_requires_expiry")
        if candidate.proposed_layer is MemoryLayer.SEMANTIC and (
            candidate.durability != "stable" or candidate.confidence < 0.85
        ):
            return WriteDecision(False, "semantic_memory_requires_stable_evidence")
        if candidate.proposed_layer is MemoryLayer.SEMANTIC and (
            event is None or "记住" not in event.content
        ):
            return WriteDecision(False, "semantic_memory_requires_explicit_consent")
        if candidate.proposed_layer is MemoryLayer.PROCEDURAL:
            return WriteDecision(False, "procedural_memory_requires_verified_outcomes")
        return WriteDecision(True)

