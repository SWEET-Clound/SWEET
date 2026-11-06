import httpx
import pytest

from memory_system.consolidation import HeuristicExtractor
from memory_system.eval import default_gold_path, evaluate_file


@pytest.mark.parametrize("bad_data", ["", '{"id":"invalid","content":"文本"}\n'])
def test_invalid_gold_fails_before_extraction(tmp_path, bad_data) -> None:
    gold = tmp_path / "gold.jsonl"
    gold.write_text(bad_data, encoding="utf-8")
    total, failures = evaluate_file(gold)
    assert total == 0
    assert failures


def test_offline_chinese_gold_set() -> None:
    total, failures = evaluate_file(default_gold_path())

    assert total >= 10
    assert failures == []


def test_gold_detects_excessive_emotion_retention() -> None:
    _, failures = evaluate_file(
        default_gold_path(), HeuristicExtractor(working_memory_ttl_hours=73),
        working_memory_ttl_hours=73,
    )
    assert failures == ["temporary-distress: expiry outside expected window for L1"]


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
