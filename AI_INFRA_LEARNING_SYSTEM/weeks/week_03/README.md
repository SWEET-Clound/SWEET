# Week 3：包结构、类型与质量门禁

> 10 小时核心 + 5 小时深化。主问题：**能否安全修改代码，而不必把整个单文件和模型服务装进脑中？**

先读 [START_HERE.md](START_HERE.md)。本周资料只从 [RESOURCES.md](RESOURCES.md) 指定段落阅读；每完成一个板块，做 [EXERCISES.md](EXERCISES.md) 对应练习并记录到 [WORK_LOG.md](WORK_LOG.md)；周末按 [RUBRIC.md](RUBRIC.md) 审计。不要把五份文件都当成需要背诵的内容。

## 本周完成的能力链

```text
明确职责 → 小模块可导入 → 类型表达契约 → 测试防回归 → 一条命令检查质量
```

完成后，当前 Agent 将从一个单文件 demo 变成一个仍然很小、但可定位职责的 package。它是后续 HTTP、异步、性能实验的容器，不是本周学习 SGLang 源码的替代品。

## 预置 Gate

- Week 2 的离线测试至少 10 个通过；
- `eval` 已不存在；
- 不提交 API Key；
- 每次结构调整前运行当前测试；不绿就不继续拆分。

## 目标结构与职责

```text
Agents/
  config.py       # 只读/校验配置
  errors.py       # 稳定错误类别与公开文案
  routing.py      # 纯输入→任务类型
  safe_calculator.py
  state.py        # AgentState 与任务类型
  tools.py        # LangChain tool 适配层
  graph.py        # create_graph：节点与边
  service.py      # run/stream 的应用接口
  study_assistant.py # 很薄的命令行入口（兼容层）
tests/
```

“拆分”不是文件越多越好。每个模块必须回答一个问题；若两个模块总一起改，说明边界可能是假象。

## 五个学习板块

### W3-1：先写 ADR，再移动代码（2 小时）

**知识：** ADR（Architecture Decision Record）是一次可回看的设计决定：背景、选项、选择、代价、后果。它不是长论文。

**路径：**

1. 阅读 `study_assistant.py`，用“配置 / 状态 / 纯规则 / LangChain 适配 / graph / 运行入口”给每段标色；
2. 在 `adr/001-agent-module-boundaries.md` 写 200–400 字：为何不一次重写、每个目标模块负责什么、哪些保持不动；
3. 写一个依赖方向图：`service → graph → tools/routing/state/config/errors`，禁止 `routing` 反向 import `graph`；
4. 先为准备移动的纯函数补测试，再移动一个模块；
5. 每次移动后跑 pytest 和 `python -m compileall Agents`。

**练习：** 为以下归属做选择并说明理由：`AgentState`、`@tool calculator`、`model.invoke`、用户错误文案、`thread_id` 组装。

**产出 / 验收：** ADR、依赖图、一次小移动的绿测记录。你能解释“模块边界按变化原因划分”，而不是按代码长度。

### W3-2：类型是可执行的沟通（3 小时）

**知识：** `TypedDict` 描述字典字段，`Literal` 限制有限字符串，`Protocol` 描述需要的行为而非具体类。类型检查不运行模型；它提前指出“调用者认为是 A、实现其实返回 B”。

**路径：**

1. 在 `state.py` 定义 `TaskType = Literal[...]`，列全合法任务；
2. 为 `classify_task_type`、配置函数、安全计算器、公开错误文案写参数与返回类型；
3. 把 `AgentState.task_type: str` 缩窄为 `TaskType`；
4. 故意写一个把 `"tool"` 当作数字返回的最小错误，先预测 mypy 应报告什么，再删掉错误；
5. 只从 `Agents/routing.py` 和 `Agents/state.py` 开始运行 mypy，先不对第三方无类型模块强行 strict。

**练习：** 写三个 `reveal_type` 预测：合法 `TaskType`、`Optional[str]` 配置值、`dict.get` 的返回。记录预测和真实 mypy 输出。

**资源：** [mypy 入门](https://mypy.readthedocs.io/en/stable/getting_started.html)，只读 annotations、incremental adoption、strict mode 三节。

**产出 / 验收：** `state.py`、至少 4 个关键函数注解、一次故意类型错误与 mypy 输出。通过不等于“全部代码 100% strict”。

### W3-3：工具适配层与输入边界（3 小时）

**知识：** 核心函数负责业务规则；适配层负责把框架输入转成核心函数可理解的值，并把异常变成稳定响应。不要在测试中绕过核心规则去测 LangChain 装饰器。

**路径：**

1. 保持 `safe_calculator.calculate()` 为纯函数；
2. 在 `tools.py` 用 `@tool` 包一层 `calculator_tool()`；
3. 明确 contract：任何异常都不能回显 Key、路径或 traceback；
4. 测纯函数的正确/错误输入；再只用 1–2 个组件测试验证 tool adapter 文案；
5. 为本地知识查询定义空白 query、未知术语、大小写匹配的边界表。

**练习：** 为 `make_study_plan(topic, days)` 设计 6 个边界：空 topic、0、负数、非整数、很大 days、正常值。先决定拒绝还是限制，再写测试，不接受“默认能跑”。

**产出 / 验收：** 至少 20 个总测试；工具核心与框架适配可单独导入；每个输入规则都有用户可见理由。

### W3-4：Ruff、mypy、pytest 质量门（3 小时）

**知识：** linter 发现可疑代码风格/错误，formatter 统一格式，type checker 检查静态契约，pytest 检查运行时行为；四者互不替代。

**路径：**

1. 在 `pyproject.toml` 集中 pytest、Ruff、mypy 最小配置；不要创建第二份隐藏配置；
2. 将 `ruff`、`mypy`、`pytest` 固定到开发依赖；
3. 分别运行 `ruff check .`、`ruff format --check .`、`mypy Agents/routing.py Agents/state.py`、`pytest -q`；
4. 对每个失败先分类：真实 bug、格式、未标注边界、第三方 stub 缺失；
5. 写 `scripts/quality.ps1` 或单一 documented command，按固定顺序跑四项；命令失败应以非零退出。

**练习：** 故意加入未使用 import、格式不一致和错误返回类型，预测哪个工具会报，再用最小改动修复。

**资源：** [Ruff 教程](https://github.com/astral-sh/ruff/blob/main/docs/tutorial.md)；[Ruff 配置](https://docs.astral.sh/ruff/configuration/)。

**产出 / 验收：** 一条质量命令在无真实模型、无 Key 情况下通过；不使用 `# noqa` 或 `# type: ignore` 掩盖自己尚未理解的问题。

### W3-5：pre-commit 与重构审计（4 小时）

**知识：** pre-commit 是本地快速反馈，不替代 CI；重构是改变内部结构且保持可观察行为。测试是“行为没有变”的证据，不是重构许可的装饰。

**路径：**

1. 配置只运行 Ruff format/check 和基础文件检查的 pre-commit；
2. 使用 `git diff` 逐段看本周变更；
3. 运行全量质量门；
4. 写 `experiments/week_03/refactor_audit.md`：旧职责、现位置、保留的 public API、未解决风险；
5. 口头演示：从 `service` 进入、如何到 `graph`、如何调用纯 router，不看代码讲 3 分钟。

**产出 / 验收：** 20+ 测试、质量门一条命令通过、ADR、重构审计。若第三方类型缺失，记录范围和原因；不得伪称全仓类型安全。

## 本周评分与停止线

| 项目 | 分数 | 证据 |
| --- | ---: | --- |
| 边界 ADR 与依赖图 | 15 | 文件与解释一致 |
| 纯模块 / 类型 | 20 | task type 与 state 有测试和类型 |
| 工具输入边界 | 20 | 20+ 测试、错误不泄密 |
| 质量门 | 25 | ruff/mypy/pytest 可重复运行 |
| 重构审计与讲解 | 20 | diff、审计报告、3 分钟讲解 |

80/100 且质量门通过，才进入 Week 4。不要因“文件拆好了”跳过测试或类型错误。
