# Week 3 练习实验室

每题先在 `WORK_LOG.md` 写预测，再写/运行测试。目标路径可按真实项目调整；命令中的 `.venv` 必须从项目根目录执行。

## P1：模块责任判定（B1）

- 起始：当前 `Agents/study_assistant.py` 或实际入口；新建 `adr/001-agent-module-boundaries.md`。
- 输入：`AgentState`、环境变量读取、task 分类、`@tool`、graph compile、`model.invoke`、错误文案。
- 预期：每项归属一个职责模块，并附“变化原因”；依赖图中 `routing` 不依赖 `graph`。
- 验证：`.\.venv\Scripts\python.exe -m pytest -q` 仍绿；人工检查 ADR 有非目标。
- 反例：按“文件太长”拆分、让 router 调 model 都失败。
- 提示：问“它因什么而变化”，不是“它现在写在哪”。

## P2：重构行为守恒（B2）

- 起始：选择一个无 I/O 的 router 或 calculator；目标 `Agents/routing.py` 与 `tests/test_routing.py`。
- 输入：至少正常、空白、未知三组输入。
- 预期：移动前后返回值/异常类型相同；`import Agents.routing` 不打印、不联网、不创建模型。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_routing.py`，再全量 pytest。
- 反例：循环 import 或 module 顶层创建 graph；测试应揭露/导入失败。
- 自检：你能说出为何“先 copy、绿后 delete”比一次剪切安全。

## P3：类型拒绝非法路由（B3）

- 起始：`Agents/state.py`、`Agents/routing.py`。
- 输入：临时让标注为 `TaskType` 的函数返回 `"route_to_mars"`。
- 预期：mypy 在该 return 报 incompatible return；修复为 allowlist 后通过。
- 命令：`.\.venv\Scripts\python.exe -m mypy Agents/state.py Agents/routing.py`。
- 反例：把返回类型改回 `str` 让错误消失；这不算修复。
- 自检：类型检查不取代运行时输入校验，理由是外部 JSON 仍可能错误。

## P4：输入边界参数化（B4）

- 起始：纯 `make_study_plan(topic, days)`；`tests/test_study_plan.py`。
- 输入：`("",3)`、`("KV",0)`、`("KV",-1)`、`("KV",1.5)`、`("KV",10000)`、`("KV",3)`。
- 预期：前五项由你定义的稳定错误/限制处理；最后一项返回确定计划；异常不得含 Key/path/traceback。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_study_plan.py`。
- 反例：`days=True` 或空白但只空格；至少选择一个加入测试。
- 提示：先完成 contract 表，再写 if。

## P5：质量工具辨认（B5）

- 起始：任意临时练习文件，结束前删除演示错误。
- 输入：未用 import、格式故意错、标注 `-> int` 却 return string。
- 预期：分别由 Ruff check、Ruff format check、mypy 报告；pytest 不一定抓到静态返回类型。
- 命令：按 `scripts/quality.ps1` 或 README 的四条命令。
- 反例：`# noqa`/`# type: ignore` 隐藏错误；不允许作为答案。
- 自检：记录每个工具的第一条报错和修复。

## P6：三分钟证据讲解（连接题）

不看代码解释：一次输入如何经过 router、类型/测试如何保护、质量门为何仍必要。录音或写 200 字讲稿；用 Rubric 口试题自评。答不出时回到对应 B，而不是开启 Week4。
