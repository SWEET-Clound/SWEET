"""S2：五种 pytest 测试技巧的最小例子。

运行方式（在仓库根目录，也就是本文件的上两级）：

    .\.venv\Scripts\python.exe -m pytest -q

或者安装好 pytest 后直接：

    pytest -q

技巧清单（对应 README「S2 → 练习」的五条）：
    1. 普通返回值测试      —— assert
    2. pytest.raises 测试  —— 断言会抛异常
    3. 参数化测试          —— @pytest.mark.parametrize
    4. Mock(side_effect)   —— mock 外部边界（模型/网络）
    5. monkeypatch 环境变量 —— 临时改环境变量，测完自动恢复
"""
import pytest
from unittest.mock import Mock, patch

from practice.s2_demo import (
    calculator,
    divide,
    get_api_key,
    make_study_plan,
    summarize,
)


# ============================================================
# 技巧 1：普通返回值测试（assert）
# 三段式 Arrange-Act-Assert：准备 → 执行 → 断言
# ============================================================

def test_divide_returns_float():
    # Arrange（准备输入）
    a, b = 10, 4
    # Act（执行被测逻辑）
    result = divide(a, b)
    # Assert（断言结果符合预期）
    assert result == 2.5


def test_make_study_plan_produces_one_line_per_day():
    plan = make_study_plan("LangGraph", days=3)
    lines = plan.strip().split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("Day 1:")
    assert lines[2].startswith("Day 3:")


# ============================================================
# 技巧 2：pytest.raises（断言异常）
# with pytest.raises(异常类型):  —— 只有「恰好抛出该异常」才通过；
# 不抛、或抛出别的异常类型，测试都失败。
# ============================================================

def test_divide_by_zero_raises_zero_division_error():
    with pytest.raises(ZeroDivisionError):
        divide(1, 0)


def test_raises_wrong_exception_type_fails_to_catch():
    # 抛的是 ZeroDivisionError，用 ValueError 去接是不匹配的——
    # 这个测试用来提醒你：raises 的异常类型必须精确。
    # （这里故意注释掉，避免它把整个测试套件标红；你可以自己解开试一次。）
    # with pytest.raises(ValueError):
    #     divide(1, 0)
    assert True  # 占位：真正的演示见上方注释


# ============================================================
# 技巧 3：参数化测试（parametrize）
# 一份测试逻辑，跑多组输入/期望，pytest 会为每组生成一个独立用例。
# ============================================================

@pytest.mark.parametrize(
    "expression, expected",
    [
        ("3 + 4 * 10", "43"),          # 正常算术
        ("10 / 4", "2.5"),             # 除法
        ("(2 + 2) * 3", "12"),         # 括号
        ("3 # 5", "Error: expression contains unsupported characters."),  # 非法字符
        ("1 / 0", "Calculation error: division by zero"),                  # 除零
    ],
)
def test_calculator(expression, expected):
    assert calculator(expression) == expected


@pytest.mark.parametrize("days", [1, 2, 5])
def test_make_study_plan_line_count_matches_days(days):
    plan = make_study_plan("pytest", days=days)
    assert len(plan.split("\n")) == days


# ============================================================
# 技巧 4：Mock(side_effect=...)（隔离外部边界）
# 用 Mock 替换 ask_model，测试 summarize 的行为而不触发真实模型。
# side_effect 可以是：返回值、异常、函数、列表（依次返回）。
# ============================================================

def test_summarize_returns_mocked_model_answer():
    # patch("practice.s2_demo.ask_model", ...) 把被测模块里的 ask_model 替换成 mock。
    # return_value 是「每次都返回固定值」的简化写法。
    fake = Mock(return_value="这是模型的假回答")
    with patch("practice.s2_demo.ask_model", fake):
        result = summarize("什么是 LangGraph？")
    assert result == "这是模型的假回答"
    fake.assert_called_once_with("什么是 LangGraph？")


def test_summarize_propagates_model_timeout_via_side_effect():
    # side_effect 设为异常：模拟「模型超时」。被测代码若没处理，异常会正常向上传播。
    with patch("practice.s2_demo.ask_model", side_effect=TimeoutError("模型超时")):
        with pytest.raises(TimeoutError):
            summarize("hi")


def test_summarize_side_effect_as_function_returns_different_values():
    # side_effect 也可以是函数：每次调用执行它。
    calls = []
    def fake_model(prompt):
        calls.append(prompt)
        return prompt.upper()

    with patch("practice.s2_demo.ask_model", side_effect=fake_model):
        assert summarize("hello") == "HELLO"
    assert calls == ["hello"]


# ============================================================
# 技巧 5：monkeypatch 环境变量
# monkeypatch 是 pytest 内置 fixture：setenv/delenv 后测试结束自动还原，
# 不会污染别的测试（这就是「测试之间不共享状态」的保证之一）。
# ============================================================

def test_get_api_key_reads_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-123")
    assert get_api_key() == "sk-fake-123"


def test_get_api_key_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert get_api_key() is None


# ============================================================
# 附：一个「必然失败 → 修正」的 Red-Green 演示思路
# 你可以把下面断言临时改成错误值，先看它 Red，再改回来 Green。
# ============================================================

def test_calculator_simple_addition():
    assert calculator("1 + 1") == "2"
