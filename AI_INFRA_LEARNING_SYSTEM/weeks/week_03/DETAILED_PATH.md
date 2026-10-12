# Week 3 逐次执行手册：模块边界、类型与质量门

> 核心 10h + 深化 5h。先读 [README](README.md) 理解概念；本文件只负责让你按步骤执行。通用规则见 [学习包执行标准](../../LEARNING_PACKET_STANDARD.md)。

## 本周知识地图

| 概念 | 它解决什么 | 当前项目中的最小位置 | 反例/危险信号 |
| --- | --- | --- | --- |
| 模块边界 | 将变化隔离，降低改动半径 | `Agents/routing.py` 只做输入→任务分类 | `routing.py` import graph 或调用模型 |
| 依赖倒置/注入 | 让测试不用真实模型 | `service` 接受一个可替换的 runner | 测试 patch 全局环境或真实网络 |
| `Literal`/`TypedDict` | 把合法状态写成契约 | `TaskType`、`AgentState` | 用随意的 `str` 传递路由值 |
| characterization test | 重构前冻结可观察行为 | router/calculator 的输入输出表 | 先搬文件，事后才发现行为变了 |
| quality gate | 让风格、类型、运行时错误各自被发现 | `quality.ps1` | “pytest 绿，所以类型与格式无需检查” |

## 进入 Gate 与目标文件

在仓库根目录执行：

```powershell
git status --short
.\.venv\Scripts\python.exe -m pytest -q
```

记录输出。若 pytest 不绿：只修现有失败，不做重构。若当前项目没有 `Agents/` 或命名不同，在 Work Log 写真实路径，并把下表路径作**职责映射**，不是盲目新建同名文件。

| 目标 | 首选文件 | 最小完成定义 |
| --- | --- | --- |
| 决策记录 | `adr/001-agent-module-boundaries.md` | 200–400 字、依赖箭头、非目标 |
| 稳定状态 | `Agents/state.py` | `TaskType` 与状态字段可 import |
| 纯路由 | `Agents/routing.py` | 不 import graph/model/环境变量 |
| 纯计算 | `Agents/safe_calculator.py` | 输入非法时抛/返回已定义错误 |
| 框架适配 | `Agents/tools.py` | 只转换框架输入/输出 |
| 应用入口 | `Agents/service.py` | 可用 fake runner 调用 |
| 回归保护 | `tests/test_routing.py` 等 | 先红后绿、离线运行 |

## Session 1（2h）：冻结旧行为并画边界

1. 读 `study_assistant.py`（或当前入口），将每个函数按“配置、状态、纯规则、框架适配、graph、CLI”标注到 ADR。
2. 写 6 行依赖图，例如 `service → graph → tools → routing/state`。每条箭头解释“谁依赖谁、为何不能反向”。
3. 从纯函数开始写 characterization tests：正常、空白、未知/非法输入各一例；此时**不得移动代码**。
4. 运行：`.\.venv\Scripts\python.exe -m pytest -q tests/test_routing.py`（换成真实测试文件）。
5. 预期：至少一条初始红测后变绿，且没有网络请求。若难以 import：把模块顶层副作用移到 `main()` 或 factory，先为“import 不打印/不联网”写测试。

**停止线**：ADR 有非目标“本周不改变模型/提示词/用户行为”，并能解释一个反向依赖为什么危险。

## Session 2（2h）：一次只移动一个纯模块

1. 在新文件复制（不要剪切）最小纯函数和类型；先让新文件单独 import。
2. 让旧入口临时 import 新实现，运行单测；绿后才删旧重复代码。
3. 增加测试：`test_import_has_no_network_or_print_side_effect`（使用适当捕获方式）；再跑全量 pytest。
4. 在 Work Log 写“重构前后输入/输出完全相同”的表，至少 3 行。

**失败分支**：循环 import → 把共享类型移入 `state.py`，不要让低层 import 高层；行为变了 → 回到 characterization tests，而非更改断言适配新行为。

## Session 3（2h）：把类型当作可检查的设计

1. 写 `TaskType = Literal[...]`，列出当前实际分支；不为未来猜测添加值。
2. 给 router、calculator、配置读取、公开错误格式添加参数与返回标注。
3. 临时写 `return "route_to_mars"`，先预测错误位置，然后运行：

```powershell
.\.venv\Scripts\python.exe -m mypy Agents/routing.py Agents/state.py
```

4. 删除故意错误，保留 mypy 输出摘要到 Work Log。第三方无 stub 时仅记录模块名和原因，不用宽泛 `ignore_missing_imports = True` 掩盖全项目。

**自检问答**：`TypedDict` 何时在运行时拒绝缺字段？答案：通常不；它主要帮助静态分析，所以仍需要运行时输入校验。

## Session 4（2h）：工具适配与输入边界

1. 为 `make_study_plan(topic, days)` 写 contract 表：空白、0、负数、小数、过大、正常值。
2. 为每格先写测试，测试名示例 `test_make_study_plan_rejects_blank_topic`；再实现最小错误类型/用户文案。
3. 若使用 LangChain tool，只测试 1–2 个适配行为；核心规则必须在无 LangChain import 情况下可测。
4. 运行单文件、再全量 pytest。预期：异常中不含 Key、路径或 traceback。

## Session 5（2h）：建立质量门与审计

1. 在 `pyproject.toml` 集中配置 pytest、Ruff、mypy；在依赖文件中锁定 dev 工具的兼容版本。先用 `python -m pip show pytest ruff mypy` 记录版本。
2. 创建/完善 `scripts/quality.ps1`，顺序必须为：`ruff check` → `ruff format --check` → scoped mypy → pytest。每一条的非零退出都使脚本失败。
3. 故意制造未用 import、格式变化、错误返回类型，预测并分别由哪个工具发现；逐个撤销这些演示错误。
4. 写 `experiments/week_03/refactor_audit.md`：保留 API、已测行为、未覆盖风险、回滚策略。

**完成命令（示例）**：`powershell -ExecutionPolicy Bypass -File scripts/quality.ps1`。成功定义是四步都绿；若项目没有该脚本，逐条命令同样要完整记录。

## 深化（最多 5h）与交付清单

- 为每个目标模块画一张“输入/输出/禁止依赖”卡；只选一个真实第三方适配层加 Protocol。
- 执行 [SGLang Lab](SGLANG_LAB.md)；建立价值资产 schema 草图。
- 最终检查：20+ 离线测试、ADR、前后行为表、质量命令、SGLang 阅读卡。缺任意硬项，不进入 Week 4。
