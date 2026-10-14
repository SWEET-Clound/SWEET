# pytest 学习笔记（S2）

> 目标：会用 pytest 写并运行基本测试，掌握五种测试技巧。
> 配套代码：`practice/s2_demo.py`（被测模块）+ `tests/test_s2_demo.py`（测试）。

## 1. 三个核心概念

### assert —— 断言
- pytest 直接使用 Python 的 `assert 表达式` 语句，无需 `self.assertEqual(...)`。
- 失败时 pytest 会「断言重写」，自动展开左右两边的值，报告「期望 vs 实际」。
- 语法就是三段式 **Arrange–Act–Assert**：准备输入 → 执行 → 断言结果。

```python
def test_divide_returns_float():
    result = divide(10, 4)   # Arrange + Act
    assert result == 2.5      # Assert
```

### raises —— 断言异常
- `with pytest.raises(异常类型):` 只有「恰好抛出该类型异常」才通过。
- 不抛、或抛出别的类型，测试都失败。异常类型必须精确。

```python
with pytest.raises(ZeroDivisionError):
    divide(1, 0)
```

### discovery —— 测试发现
- pytest 自动查找测试，无需注册。默认规则：
  - 文件名匹配 `test_*.py` 或 `*_test.py`；
  - 函数/方法名以 `test_` 开头。
- 在仓库根目录运行 `pytest`，它会递归扫描并收集所有测试。

## 2. 五种测试技巧

| # | 技巧 | 关键机制 | 作用 |
| --- | --- | --- | --- |
| 1 | 普通返回值测试 | `assert` | 校验纯函数的输出 |
| 2 | 异常测试 | `pytest.raises` | 校验失败路径抛对异常 |
| 3 | 参数化 | `@pytest.mark.parametrize` | 一份逻辑跑多组输入，减少重复 |
| 4 | mock 外部边界 | `Mock(side_effect=...)` / `patch` | 隔离模型/网络等不可控依赖 |
| 5 | 环境变量 | `monkeypatch.setenv/delenv` | 临时改环境，测完自动恢复 |

完整可运行例子都在 `tests/test_s2_demo.py`，每个技巧都有一到多个测试函数，测试名直接说明行为（如 `test_divide_by_zero_raises_zero_division_error`）。

## 3. 应该 mock / 不应该 mock

| 应该 mock（隔离不可控边界） | 不应该 mock（测真实逻辑） |
| --- | --- |
| 外部 LLM 调用（DeepSeek 的 `model.invoke`）——慢、花钱、要 key、不稳定 | 被测的纯逻辑本身（路由判断、计算、计划生成） |
| 网络请求 / HTTP API | 简单的值对象、数据结构 |
| 数据库读写、文件系统 | 你拥有且稳定的内部工具函数 |
| 时间（`time.sleep`、`datetime.now`）——慢、不可复现 | 纯粹为了「让测试变绿」而 mock 掉被测对象 |
| 随机数——结果不可复现 | 断言本身（把预期写死成被测逻辑的复制品） |

判断口诀：**mock 的是「被测模块查找该对象的位置」，目标是隔离边界，而不是让测试永远变绿。**

## 4. Red–Green–Refactor

1. **Red**：先写一个必然失败的测试（例如把预期写错），跑一遍确认它红；
2. **Green**：写最少代码（或修正预期）让测试通过；
3. **Refactor**：在不改变行为的前提下整理代码，测试保持绿。

`tests/test_s2_demo.py` 末尾的 `test_calculator_simple_addition` 就是练习入口：把 `"2"` 临时改成 `"3"` 看它失败，再改回来。

## 5. 仍未理解的问题

- （留白，写下你口试前想确认的点）
