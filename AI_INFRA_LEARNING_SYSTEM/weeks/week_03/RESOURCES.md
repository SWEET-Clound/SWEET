# Week 3 资料索引与阅读边界

总阅读不超过 3 小时。所有资料都要写：一句概念、当前项目位置、最小例子、一个问题。

| 优先级 | 资料 | 只读范围 | 学完必须能做什么 | 对应板块 |
| --- | --- | --- | --- | --- |
| 必读 | [Python modules](https://docs.python.org/3/tutorial/modules.html) | module、package、import | 解释文件导入为何会执行顶层语句 | W3-1 |
| 必读 | [typing](https://docs.python.org/3/library/typing.html) | `Literal`、`TypedDict`、`Protocol` | 为任务类型/状态写 contract | W3-2 |
| 必读 | [mypy getting started](https://mypy.readthedocs.io/en/stable/getting_started.html) | annotations、incremental adoption | 跑指定模块的 type check | W3-2/4 |
| 必读 | [Ruff tutorial](https://github.com/astral-sh/ruff/blob/main/docs/tutorial.md) | check、format | 区分 lint 与 format | W3-4 |
| 选读 | [Ruff configuration](https://docs.astral.sh/ruff/configuration/) | `pyproject.toml` 配置结构 | 只保留一份质量配置 | W3-4 |
| 必读 | [pre-commit](https://pre-commit.com/) | install、run、hooks | 解释本地 hook 不替代 CI | W3-5 |
| 原则参考 | [pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html) | `setenv`、`setattr` | 说明何时应注入依赖而不是 patch 全局 | W3-3 |

## 本周不读

- Python import machinery 全文；
- 完整 SOLID/设计模式课程；
- 所有 Ruff rule；
- 第三方 LangChain 类型 stub 的细节；
- CI 平台和 Docker。

它们不能帮助本周的硬门槛：小而清楚的 package、20+ 测试与一条质量命令。
