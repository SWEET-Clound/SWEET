# 第 1 周：建立可验证的学习基线

> 周目标：让当前 Agent 从“只有手工演示”进入“环境可描述、核心逻辑可离线测试、失败可分类、执行可追踪”的状态。  
> 默认投入：15 小时。  
> 硬门槛：Python 环境可重建、WSL2 状态明确、至少 5 个离线测试通过、10 个失败模式、1 条完整执行 trace、周报完成。  
> 本周不做：SGLang 安装、CUDA Toolkit 安装、模型微调、网页前端、数据库、项目大规模拆包。

## 0. 已知起点

2026-09-16 自动检查结果：

| 项目 | 当前状态 | 本周目标 |
| --- | --- | --- |
| Git | `2.55.0.windows.5` | 能查看状态、差异和提交历史 |
| Windows Python | `python`/`py` 均不可用 | 安装 CPython 3.13，建立 `.venv` |
| WSL | 尚未完成安装 | 安装 WSL2 Ubuntu，确认版本 |
| GPU | RTX 5070 Ti，16303 MiB，驱动 591.86 | Windows/WSL 均能运行 `nvidia-smi` |
| Agent | 单文件、导入时初始化真实模型 | 建立最小测试接缝，不访问网络也能测试 |
| 自动测试 | 未发现 | 至少 5 个测试用例通过 |

如果实际状态与表格不同，优先更新 [环境证据模板](ENVIRONMENT_EVIDENCE.md)，不要默默绕过。

## 1. 本周完成定义

以下全部满足才算完成：

- [ ] `templates/BASELINE.md` 的核心字段已填写；
- [ ] Python 版本、解释器路径、pip 版本、Git 版本有记录；
- [ ] `.venv` 可以删除后按文档重建；
- [ ] `.venv`、密钥、缓存不会进入 Git；
- [ ] WSL2 Ubuntu 已安装，或阻塞原因与下一步有权威记录；
- [ ] WSL 内可以看到 RTX 5070 Ti；
- [ ] 导入被测模块时不调用真实模型、不访问网络；
- [ ] 至少 5 个核心逻辑测试通过；
- [ ] 每个测试名称说明一个行为，而不是 `test_1`；
- [ ] 至少 10 个失败模式已分类；
- [ ] 一条输入的节点、路由、状态变化已被完整记录；
- [ ] 完成 [掌握度评分](RUBRIC.md)，总分至少 80；
- [ ] 完成周报，并确定第 2 周唯一主问题。

## 2. 时间安排

| 学习单元 | 时间 | 核心问题 | 主要产出 |
| --- | ---: | --- | --- |
| S0 启动与规则 | 1h | 本周如何证明进步？ | 基线、目录、时间表 |
| S1 环境与 GPU | 2.5h | 环境能否被重建？ | Python/WSL/GPU 证据 |
| S2 pytest 基础 | 2h | 什么是有效测试？ | 测试练习、概念卡 |
| S3 建立测试接缝 | 3h | 如何不调用模型测试 Agent？ | 最小重构、5+ 测试 |
| S4 失败模式 | 2h | 系统可能怎样失败？ | 失败目录、优先级 |
| S5 执行追踪 | 2h | 一次请求实际经过哪里？ | trace、状态变化图 |
| S6 验收与复盘 | 2.5h | 是否真正掌握？ | 口试、评分、周报 |

不要求按自然日完成。每次学习结束必须保存产出；没有产出的“看资料”不计入有效时间。

## 3. S0：启动与规则（1 小时）

### 学习目标

- 理解本周不是学习更多 Agent API，而是建立可验证的工程基线；
- 能区分“学习活动”和“掌握证据”；
- 确定真实可投入时间。

### 执行

1. 复制 `../../templates/BASELINE.md` 为 `baseline-week01.md`；
2. 填写每周稳定时间、预算、硬件和自评分；
3. 建立本周个人日历块；
4. 阅读本文件和 [评分标准](RUBRIC.md)；
5. 执行：

```powershell
git status --short --branch
git log --oneline -5
git remote -v
```

### 产出

- `baseline-week01.md`；
- 本周 6～7 个学习时间块；
- `ENVIRONMENT_EVIDENCE.md` 初始版本。

### 掌握检查

不看文档回答：

1. 为什么“看完 pytest 教程”不能证明会测试？
2. 本周最重要的三个外部证据是什么？
3. 哪些已有未跟踪文件不属于本周，为什么不能随意删除？

三个问题都能具体回答，达到 E1；能指出自己项目中的对应例子，达到 E2。

## 4. S1：环境、WSL 与 GPU（2.5 小时）

### 学习资料

只读与本次任务直接相关的段落：

1. [Python on Windows](https://docs.python.org/3/using/windows.html)：安装管理器、命令发现；
2. [Python venv](https://docs.python.org/3/library/venv.html)：创建、激活、环境不可移动；
3. [Microsoft WSL 安装](https://learn.microsoft.com/en-us/windows/wsl/install)：安装、重启、`wsl -l -v`；
4. [NVIDIA CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/)：只读 3.1–3.3；特别注意不要在 WSL 安装 Linux 显示驱动。

### 资料掌握目标

- 能解释系统 Python、虚拟环境和项目依赖的关系；
- 知道 `.venv` 为什么不能提交或复制；
- 能解释 WSL1 与 WSL2 在本路线中的选择；
- 知道 Windows NVIDIA 驱动如何向 WSL 暴露 GPU；
- 知道本周为什么不安装 CUDA Toolkit。

### 执行 A：Windows Python

按 Python 官方 Windows 页面安装 64 位 CPython 3.13。安装后打开新 PowerShell：

```powershell
py -0p
py -3.13 --version
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip --version
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable); print(sys.version)"
```

不要依赖激活脚本；后续验收命令优先使用解释器绝对相对路径 `.\.venv\Scripts\python.exe`。

### 执行 B：WSL2

这一步需要管理员 PowerShell并可能重启：

```powershell
wsl --install -d Ubuntu-24.04
```

重启、完成 Linux 用户创建后，在 PowerShell 验证：

```powershell
wsl --status
wsl -l -v
wsl nvidia-smi
```

本周只验证 GPU 可见性。**不要在 WSL 安装 NVIDIA Linux 显示驱动，也不要安装 CUDA Toolkit。**

### 执行 C：Git 忽略审计

确认以下内容不会被提交：

```text
.venv/
.env
__pycache__/
.pytest_cache/
```

只审计和提出必要的最小修改；不要处理 `.idea.local-backup-20260913/` 或 `main.py` 等用户已有未跟踪内容。

### 产出

- 填写完成的 `ENVIRONMENT_EVIDENCE.md`；
- 可重建 Python 环境命令；
- WSL 版本和 GPU 证据；
- 若失败：完整错误、已读官方故障页、下一步。

### 验收

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import sys; assert sys.prefix != sys.base_prefix"
wsl -l -v
wsl nvidia-smi
```

四项成功为 E2；能删除并按文档重建 `.venv` 后测试仍通过，才是 E3。

## 5. S2：pytest 与测试思维（2 小时）

### 学习资料

1. [pytest Get Started](https://docs.pytest.org/en/stable/getting-started.html)：安装、发现、assert、raises；
2. [pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)：只读 `setattr`、`setenv`、自动恢复；
3. [unittest.mock](https://docs.python.org/3/library/unittest.mock.html)：只读 `Mock`、`return_value`、`side_effect`、`assert_called_with`；
4. [LangGraph 测试思路](https://docs.langchain.com/oss/javascript/langgraph/test)：只提取“每个测试创建新 graph/checkpointer”和“节点可单测”两项原则；示例是 JavaScript，不复制代码。

### 资料掌握目标

- 能写 Arrange–Act–Assert；
- 能区分单元测试、集成测试、端到端测试；
- 知道 mock 的目标是隔离不可控边界，不是让测试永远变绿；
- 知道应该 patch “被测模块查找该对象的位置”；
- 理解测试之间不能共享会话状态。

### 练习

先写一个必然失败的小测试，再修正预期或实现，完整经历一次 Red–Green–Refactor。随后完成：

- 一个普通返回值测试；
- 一个 `pytest.raises` 测试；
- 一个参数化测试；
- 一个 `Mock(side_effect=...)` 测试；
- 一个环境变量 `monkeypatch` 测试。

这些练习可以放在临时学习文件中，但必须保留最终版本和命令输出。

### 产出

- `notes-pytest.md`：不超过两页；
- 五种测试技巧的最小例子；
- 一张“应该 mock / 不应该 mock”表。

### 验收口试

1. 为什么不应该在单元测试中调用 DeepSeek？
2. `return_value` 和 `side_effect` 的区别是什么？
3. 为什么每个 graph 测试应该使用新的 checkpointer/thread？
4. 一个测试通过，能证明什么、不能证明什么？

四题全部能结合当前 Agent 回答，达到 E2。

## 6. S3：为当前 Agent 建立测试接缝（3 小时）

### 目标

在不进行大规模架构重写的前提下，使 `study_assistant.py` 的纯逻辑可以在无 API Key、无网络条件下导入和测试。

### 当前阻塞

文件导入时立即执行：

- `init_chat_model(...)`；
- `model.bind_tools(...)`；
- graph compile。

这让简单的路由测试也依赖真实模型配置。需要建立最小接缝，例如把模型/graph 创建移动到显式工厂或延迟初始化。具体实现可以调整，但必须满足：**导入纯逻辑不产生网络调用和外部副作用。**

### 本周目标测试

至少覆盖以下行为：

1. 计算类输入路由到 `tool`；
2. 学习计划输入路由到 `study_plan`；
3. LangGraph 概念输入路由到 `local_knowledge`；
4. 空白输入路由到 `unknown`；
5. 普通输入路由到 `chat`；
6. 未知 `task_type` 进入 fallback；
7. 本地知识命中时返回对应条目；
8. 未命中时返回明确提示；
9. 学习计划天数与输出行数一致。

最低要求是 5 个独立行为，推荐完成 9 个。允许使用参数化，但评分按覆盖的行为而不是测试函数数量。

### 测试质量约束

- 不调用真实 LLM；
- 不需要 `DEEPSEEK_API_KEY`；
- 不依赖测试执行顺序；
- 不共享 `thread_id` 状态；
- 测试名称表达行为；
- 失败输出能指出预期与实际；
- 不为了测试而复制生产逻辑到测试里。

### 产出

- 最小 test seam；
- `tests/` 中至少 5 个行为测试；
- 一条运行全部测试的命令；
- 一段说明：为什么这不是过度重构。

### 验收

在临时移除 API Key 的进程中运行：

```powershell
Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest -q
```

测试全部通过且没有网络访问，达到 E3。只能在真实密钥存在时通过，最多 E1。

## 7. S4：失败模式目录（2 小时）

### 学习目标

- 区分输入错误、配置错误、依赖错误、模型错误、工具错误和状态错误；
- 为失败指定“如何观察”和“由谁处理”；
- 不把捕获所有 `Exception` 当成错误处理完成。

### 执行

使用 [失败目录模板](FAILURE_CATALOG.md)，至少分析 10 项。候选起点：

- API Key 缺失；
- 模型名/供应商不受支持；
- 模型超时或 429/500；
- 模型返回非法 tool call；
- 除零；
- 计算表达式超长或恶意；
- 空输入；
- 本地知识未命中；
- 同一 `thread_id` 意外串话；
- 工具循环不终止；
- checkpointer 状态污染；
- 流式处理中途异常。

不要只列名称。每项必须写触发、影响、当前行为、期望行为、观察信号、优先级和未来测试。

### 产出

- 至少 10 行失败目录；
- P0/P1/P2 优先级；
- 选出第 2 周首先修复的 3 个问题。

### 掌握检查

随机抽三项，能说明“错误发生在哪一层、当前用户看到什么、日志应记录什么、哪个测试证明修复”，达到 E3。

## 8. S5：一次请求的执行追踪（2 小时）

### 学习资料

- [Python Logging HOWTO](https://docs.python.org/3/howto/logging.html)：只读 basic logging、level、logger、格式；
- [LangGraph graph evaluation](https://docs.langchain.com/langsmith/evaluate-graph)：只读 end-to-end、intermediate step、individual node 三种评测层级。

### 学习目标

- 区分日志、指标、trace；
- 知道一次请求至少需要 request/thread 标识、节点、开始/结束、耗时、结果状态；
- 不在日志中输出 API Key、完整敏感输入或内部异常机密。

### 执行

选择一个不需要真实模型的输入，例如：

```text
bind_tools 有什么作用？
```

使用 [追踪模板](TRACE.md) 记录：

```text
输入
→ initial_state
→ router_node
→ task_type
→ route_by_task_type
→ knowledge_node
→ AIMessage
→ END
```

记录每一步读取字段、写入字段、分支理由和潜在失败。可以增加最小结构化日志，但本周不引入完整观测平台。

### 产出

- 一份完整 trace；
- 一张 Mermaid 或文本时序图；
- 至少三个可观测性缺口；
- 一条“不应记录的数据”清单。

### 验收

只看 trace 就能回答：请求走了哪些节点、为什么选择这条边、消息增加了几条、`llm_calls` 是否变化、哪里可能失败。全部能回答达到 E3。

## 9. S6：验收、口试与周报（2.5 小时）

### 执行顺序

1. 从新 PowerShell 执行环境和测试命令；
2. 按 [评分标准](RUBRIC.md) 自评并附证据；
3. 完成下面 10 道口试；
4. 复制周报模板并填写；
5. 更新 `SCORECARD.csv`；
6. 只确定第 2 周的唯一主问题，不提前展开第二周计划。

### 口试题

1. 虚拟环境解决什么问题，不解决什么问题？
2. 为什么用 `.venv\Scripts\python.exe` 比只写 `python` 更可审计？
3. 单元测试与端到端测试的边界是什么？
4. 为什么测试不应访问真实 LLM？
5. mock 过多会导致什么假象？
6. 当前模块导入副作用是什么，测试接缝如何解决？
7. `router_node` 的输入、输出和不变量是什么？
8. 同一 `thread_id` 会带来什么测试污染？
9. 日志、指标和 trace 有什么区别？
10. 本周最重要的一个失败发现是什么，它如何改变第 2 周？

回答必须结合当前代码。只背定义不算通过。

## 10. 8～10 小时压缩版

不能删除硬门槛，只缩小阅读和测试数量：

| 内容 | 时间 |
| --- | ---: |
| 基线和环境 | 2h |
| pytest 必读与练习 | 1.5h |
| 最小测试接缝和 5 个行为 | 3h |
| 10 个失败模式 | 1h |
| 单条 trace | 1h |
| 验收和周报 | 1.5h |

若少于 8 小时，本周延长，不把未完成任务滚入第二周并同时增加新内容。

## 11. 向 Codex 发起执行协作

每次开始一个单元，可以直接使用：

```text
开始执行第一周 S__。先检查当前仓库和上一单元证据；在范围内直接实现并验证。
不要提前做第二周内容。结束时按 Week 01 的产出和掌握标准审计，并告诉我还需要亲自回答的问题。
```

本周结束使用：

```text
对第一周进行完成审计。逐项检查环境、离线测试、失败目录、trace、评分和周报的权威证据。
没有证据的项目判为未完成，不要仅凭代码存在判定掌握。
```
