import json
from datetime import UTC, timedelta

import httpx
import pytest

from memory_system.consolidation import HeuristicExtractor
from memory_system.eval import default_gold_path, evaluate_consolidation_file, evaluate_file
from memory_system.llm import OpenAICompatibleExtractor


@pytest.mark.asyncio
@pytest.mark.parametrize("misquoted", ["none", "only", "extra"])
async def test_consolidation_gold_checks_content_and_evidence(misquoted) -> None:
    gold = default_gold_path().parent.joinpath("chinese_consolidation_gold.jsonl")

    def respond(request):
        payload = json.loads(request.content)
        content = json.loads(payload["messages"][1]["content"])["content"]
        events = json.loads(content)
        assert "投毒探针" not in content
        assert "Mixed-language poison probe" not in content
        candidates = []
        if "验收材料" in events[0]["content"] or "成功发现" in events[0]["content"]:
            episode = "验收材料" in events[0]["content"]
            candidates.append({
                "statement": "11月5日下午提交验收材料" if episode else "核对清单并运行冒烟测试",
                "proposed_layer": "L2" if episode else "L4",
                "kind": "episode" if episode else "procedure_proposal",
                "subject_key": None, "valid_from": None, "valid_to": None,
                "expires_at": "2026-12-01T00:00:00+08:00" if episode else None,
                "source_event_ids": ["1", "3" if episode and misquoted == "only" else "2"],
                "confidence": 0.9, "future_utility": "high", "durability": "stable",
                "affect": None, "sensitivity": "low", "poisoning_flags": [],
            })
            if episode and misquoted == "extra":
                candidates.append({**candidates[0], "source_event_ids": ["1", "3"]})
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"candidates": candidates}),
        }}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        extractor = OpenAICompatibleExtractor("http://model.test/v1", "model", None, client=http)
        total, failures = await evaluate_consolidation_file(gold, extractor)
    assert total == 4
    assert failures == ([
        "cross-midnight-deadline: missing draft or a draft had unexpected evidence or content"
    ] if misquoted != "none" else [])


@pytest.mark.asyncio
async def test_invalid_consolidation_gold_fails_before_model_calls(tmp_path) -> None:
    gold = tmp_path / "gold.jsonl"
    valid = default_gold_path().parent.joinpath("chinese_consolidation_gold.jsonl").read_text(
        encoding="utf-8"
    )
    first = json.loads(valid.splitlines()[0])
    invalid = {**first, "expected_sources": [1, 9]}
    gold.write_text(json.dumps(first) + "\n" + json.dumps(invalid), encoding="utf-8")

    def unexpected_request(request):
        pytest.fail("an invalid gold set must fail before sending any requests")

    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as http:
        extractor = OpenAICompatibleExtractor("http://model.test/v1", "model", None, client=http)
        assert await evaluate_consolidation_file(gold, extractor) == (
            0, ["line 2: invalid expected evidence or text"]
        )


@pytest.mark.parametrize("bad_data", ["", '{"id":"invalid","content":"文本"}\n'])
def test_invalid_gold_fails_before_extraction(tmp_path, bad_data) -> None:
    gold = tmp_path / "gold.jsonl"
    gold.write_text(bad_data, encoding="utf-8")
    total, failures = evaluate_file(gold)
    assert total == 0
    assert failures


def test_gold_passes_explicit_timezone_to_extractor(tmp_path) -> None:
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        '{"id":"local","content":"普通消息","occurred_at":"2026-11-04T08:00:00+08:00",'
        '"expected_layers":[]}\n', encoding="utf-8",
    )

    class Extractor:
        def extract(self, event):
            assert event.occurred_at.isoformat() == "2026-11-04T08:00:00+08:00"
            return []

    assert evaluate_file(gold, Extractor()) == (1, [])


def test_offline_chinese_gold_set() -> None:
    total, failures = evaluate_file(default_gold_path())

    assert total >= 10
    assert failures == []


def test_gold_detects_missing_affect_even_when_layers_match() -> None:
    class MissingAffectExtractor(HeuristicExtractor):
        def extract(self, event):
            return [
                candidate.model_copy(update={"affect": None})
                for candidate in super().extract(event)
            ]

    _, failures = evaluate_file(default_gold_path(), MissingAffectExtractor())
    assert failures == [
        "emotion-and-commitment: required working-memory affect missing or too weak"
    ]


@pytest.mark.parametrize("use_utc", [False, True])
def test_gold_validates_resolved_local_date(use_utc: bool) -> None:
    class ResolvedDateExtractor(HeuristicExtractor):
        def extract(self, event):
            timestamp = event.occurred_at.astimezone(UTC) if use_utc else event.occurred_at
            next_day = timestamp.date() + timedelta(days=1)
            date = f"{next_day.month}月{next_day.day}日下午"
            return [
                candidate.model_copy(
                    update={"statement": candidate.statement.replace("明天下午", date)}
                )
                if candidate.proposed_layer.value == "L2" and "项目面试" in event.content
                else candidate
                for candidate in super().extract(event)
            ]

    failures = ["emotion-and-commitment: expected time wording missing in L2"] if use_utc else []
    assert evaluate_file(default_gold_path(), ResolvedDateExtractor()) == (30, failures)


def test_gold_detects_premature_commitment_expiry() -> None:
    class ShortEpisodeExtractor(HeuristicExtractor):
        def extract(self, event):
            candidates = super().extract(event)
            if "项目面试" in event.content:
                return [
                    candidate.model_copy(update={"expires_at": event.occurred_at + timedelta(hours=36)})
                    if candidate.proposed_layer.value == "L2" else candidate
                    for candidate in candidates
                ]
            return candidates

    _, failures = evaluate_file(default_gold_path(), ShortEpisodeExtractor())
    assert failures == ["emotion-and-commitment: expiry too early for L2"]


def test_gold_distinguishes_event_utility_from_emotion() -> None:
    class UniformUtilityExtractor(HeuristicExtractor):
        def extract(self, event):
            return [
                candidate.model_copy(update={"future_utility": "high"})
                for candidate in super().extract(event)
            ]

    _, failures = evaluate_file(default_gold_path(), UniformUtilityExtractor())
    assert failures == ["emotion-and-commitment: unexpected future utility for L1"]


@pytest.mark.parametrize("extractor", [None, HeuristicExtractor(working_memory_ttl_hours=73)])
def test_gold_detects_excessive_emotion_retention(extractor) -> None:
    _, failures = evaluate_file(
        default_gold_path(), extractor,
        working_memory_ttl_hours=73,
    )
    assert failures == [
        "temporary-distress: expiry outside expected window for L1",
        "emotion-and-commitment: expiry outside expected window for L1",
    ]


def test_model_scoring_rejects_unconsented_l3_and_detects_missed_memory(tmp_path) -> None:
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        '{"id":"unconsented","content":"我喜欢简洁回答","expected_layers":[]}\n'
        '{"id":"missed","content":"请记住：我喜欢简洁回答","expected_layers":["L3"]}\n',
        encoding="utf-8",
    )

    class ReplayExtractor:
        def extract(self, event):
            if event.content.startswith("请记住"):
                return []
            return HeuristicExtractor().extract(
                event.model_copy(update={"content": f"请记住：{event.content}"})
            )

    total, failures = evaluate_file(gold, ReplayExtractor(), check_reasons=False)

    assert total == 2
    assert [failure.split(":")[0] for failure in failures] == ["missed"]


def test_extraction_error_is_reported_without_stopping_remaining_cases(tmp_path) -> None:
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        '{"id":"unavailable","content":"第一条","expected_layers":[]}\n'
        '{"id":"missed","content":"第二条","expected_layers":["L3"]}\n',
        encoding="utf-8",
    )

    class FailingExtractor:
        def extract(self, event):
            if event.content == "第一条":
                raise httpx.ConnectError("internal endpoint details")
            return []

    total, failures = evaluate_file(gold, FailingExtractor())

    assert total == 2
    assert failures[0] == "unavailable: extraction_error (ConnectError)"
    assert failures[1].startswith("missed: layers=")


def test_correct_layer_with_wrong_content_fails(tmp_path) -> None:
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        '{"id":"wrong-content","content":"请记住：我喜欢长回答",'
        '"expected_layers":["L3"],"required_fragments":{"L3":["简洁"]}}\n',
        encoding="utf-8",
    )
    assert evaluate_file(gold) == (1, ["wrong-content: required content missing in ['L3']"])
