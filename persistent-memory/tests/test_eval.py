from memory_system.eval import default_gold_path, evaluate_file


def test_offline_chinese_gold_set() -> None:
    total, failures = evaluate_file(default_gold_path())

    assert total >= 10
    assert failures == []
