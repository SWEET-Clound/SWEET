# AI 推理系统与实时多模态学习操作系统

> 版本：v1.0  
> 建立日期：2026-09-16  
> 主航道：SGLang 生态  
> 辅助参照：vLLM、PyTorch、Triton、LiveKit Agents  
> 远期第二曲线：LeRobot / 具身智能  
> 默认投入：每周 15 小时，连续 18–24 个月  
> 复审频率：每周执行、每月校准、每季度重新决策

## 文档导航

- [方向与替代风险审计](DECISION_MEMO.md)
- [当前仓库能力基线](CURRENT_BASELINE_ASSESSMENT.md)
- [前 12 周逐周执行](NEXT_12_WEEKS.md)
- [人机协作协议](COLLABORATION_PROTOCOL.md)
- [机器可读路线状态](roadmap.yaml)
- [路线变更记录](CHANGELOG.md)
- [能力基线模板](templates/BASELINE.md)
- [周度复盘模板](templates/WEEKLY_REVIEW.md)
- [实验模板](templates/EXPERIMENT.md)
- [季度复盘模板](templates/QUARTERLY_REVIEW.md)
- [月度记分卡](SCORECARD.csv)

## 1. 最终决策

本计划押注的不是“SGLang 这个仓库永远领先”，而是以下可迁移能力组合：

1. 大模型推理原理与性能工程；
2. GPU、显存、KV Cache、并行和调度；
3. 实时语音/多模态 Agent 的端到端系统设计；
4. 可观测性、评测、故障恢复和成本工程；
5. 能进入大型开源项目协作的工程能力；
6. 一个具体行业场景中的数据、工作流和质量标准。

在满足本计划“投入假设”和“验收门槛”的条件下，对这组能力在未来 3–5 年仍具有高价值、稀缺、跨项目迁移、短期难被完全自动化替代的判断置信度为 **约 90%**。

这不是对收入、职位或创业结果的 90% 保证。它表达的是：即使 SGLang 被 vLLM、某个云平台或下一代运行时取代，计划中积累的调度、缓存、并行、性能分析、评测和实时系统能力仍可迁移。

### 1.1 90% 判断成立的前提

- 每周至少投入 12–15 小时，而不是碎片化观看视频；
- 至少 60% 时间用于编码、实验、读源码和写复盘；
- 6 个月内开始公开作品，12 个月内尝试上游贡献；
- 能接受 Linux、英文文档、数学、PyTorch 和 GPU 调试；
- 不把“安装成功”和“API 调通”算作掌握；
- 所有性能结论必须可复现并带环境、版本和原始数据；
- 最迟第 9 个月选择一个窄而深的专业化方向；
- 持续构建行业场景，不做无差异的通用聊天机器人。

如果这些条件长期不成立，本判断应自动降级，而不是靠意志维持。

## 2. 为什么这条路线难以被快速替代

容易商品化的能力：

- Prompt 编写；
- Agent 框架 API 调用；
- 普通 RAG；
- 简单模型部署；
- 从教程复制 Demo；
- 只会使用云端兼容 API。

较难商品化的能力：

- 在特定硬件和负载下解释性能瓶颈；
- 在吞吐、首 token 延迟、单 token 延迟、显存和质量之间做取舍；
- 定位非确定性、并发、缓存污染、OOM、死锁和跨节点通信问题；
- 证明优化没有破坏正确性与模型质量；
- 设计真实 workload 和可靠评测；
- 把推理服务接入实时语音、多模态、工具调用和业务状态机；
- 在大型开源项目中完成可审查、可维护的修改；
- 将技术与某个行业的流程、数据和风险控制结合。

AI 编码工具会显著提高实现速度，但它也会放大“能否定义正确问题、建立正确实验、判断结果可信度”的差距。本计划刻意把护城河放在问题定义、系统测量、正确性和跨层调试上。

## 3. 目标身份与价值主张

18–24 个月后的目标不是“会使用 SGLang”，而是：

> 能为实时、多模态和 Agent 工作负载设计、部署、测量并优化模型推理系统；能用实验数据解释取舍；能向 SGLang/vLLM 等上游提交有效贡献；能在一个具体行业建立自己的数据与评测标准。

目标能力分为六层：

| 层级 | 能力 | 最终证明 |
| --- | --- | --- |
| L1 工程底座 | Python、Linux、Git、测试、异步、网络 | 可维护仓库、CI、测试、类型和文档 |
| L2 模型基础 | Transformer、attention、采样、量化、PyTorch | 从零解释并测量一次推理过程 |
| L3 推理系统 | batching、KV cache、调度、并行、服务化 | 自建 benchmark，对比两个引擎 |
| L4 性能底层 | profiler、Triton/CUDA、通信、kernel | 定位并改善一个真实瓶颈 |
| L5 实时产品 | 语音、多模态、Agent、观测与恢复 | 可运行的真实端到端系统 |
| L6 独特价值 | 行业数据、评测、上游贡献、公开声誉 | PR、报告、用户、数据集或标准 |

### 3.1 推荐的第一专业化方向

第一专业化方向确定为：

> **Agent 与 RL workload 下的 KV Cache、调度、路由和正确性。**

原因：

- 位于 Agent 应用、推理 runtime 与 RL 后训练交界；
- 既可从 CPU test、模拟器、trace replay 起步，也能延伸到多 GPU；
- SGLang 已有 session-aware cache、P/D、RL rollout 等明确但未结束的工作；
- 同类问题同样存在于 vLLM 和未来运行时，技能可迁移；
- 可自然积累个人 workload、trace、故障语料与评测资产；
- 相比只写特定 kernel，对个人硬件要求较低；
- 相比只做 Agent 编排，系统深度与验证难度显著更高。

第二专业化方向在第 12 个月再从“实时 Omni 推理”和“异构硬件/MoE”中选择，第一年不同时重押三条线。

## 4. 生态选择

### 4.1 主项目：SGLang

选择原因：它同时覆盖高性能 LLM/VLM serving、RadixAttention、连续批处理、结构化输出、投机解码、Prefill/Decode 分离、多种并行、RL rollout、多模态生成和多硬件后端。

必须关注的官方入口：

- 仓库：https://github.com/sgl-project/sglang
- 文档：https://docs.sglang.io/
- 路线图：https://roadmap.sglang.io/
- 学习材料：https://github.com/sgl-project/sgl-learning-materials
- 贡献指南：https://github.com/sgl-project/sglang/blob/main/docs/docs/developer_guide/contribution_guide.mdx
- RL 集成：https://github.com/sgl-project/sglang/blob/main/docs/docs/advanced_features/sglang_for_rl.mdx

### 4.2 对照项目：vLLM

用途不是“两边都浅学”，而是作为原理和实验对照：

- PagedAttention 与现代 serving 基础；
- 同模型、同硬件、同 workload 下的公平比较；
- 学习两个项目不同的抽象、调度与缓存选择；
- 降低对单一仓库设计的路径依赖。

入口：

- 仓库：https://github.com/vllm-project/vllm
- 论文：https://arxiv.org/abs/2309.06180
- 文档：https://docs.vllm.ai/

### 4.3 产品入口：LiveKit Agents

用于把底层推理连接到真实系统：

- 流式音频和 WebRTC；
- STT–LLM–TTS 或端到端语音模型；
- 打断、轮次检测、延迟、重试；
- 会话状态、工具调用和可观测性；
- 实时用户体验与推理系统的联合优化。

入口：https://github.com/livekit/agents

### 4.4 第二曲线：LeRobot

仅在完成第 3 阶段后再决定是否重投入。先使用仿真和公共数据，暂不以购买硬件作为进度。

入口：https://github.com/huggingface/lerobot

## 5. 时间和执行制度

### 5.1 默认每周 15 小时

| 活动 | 小时 | 比例 |
| --- | ---: | ---: |
| 项目实现与实验 | 6 | 40% |
| 源码阅读与调试 | 3 | 20% |
| 基础知识与论文 | 2.5 | 17% |
| 测试、文档、复盘 | 2 | 13% |
| 社区、Issue、PR | 1.5 | 10% |

忙碌周的最低版本为 6 小时：3 小时实现、1 小时阅读、1 小时测试、1 小时复盘。连续两周低于 6 小时，必须缩小当月目标，禁止继续增加课程。

### 5.2 每周固定循环

1. 周一：确定一个可证伪问题和验收指标；
2. 周二至周四：实现、实验、记录失败；
3. 周五：读与问题直接相关的源码；
4. 周末：整理数据、测试、写周报、决定下一步；
5. 每周只允许一个主目标，其他想法进入 backlog。

### 5.3 学习证据等级

| 等级 | 证据 | 是否算掌握 |
| --- | --- | --- |
| E0 | 看过、听过 | 否 |
| E1 | 能解释术语 | 否 |
| E2 | 独立跑通并修改 | 初步 |
| E3 | 有测试和可复现实验 | 是 |
| E4 | 能定位失败、比较方案 | 熟练 |
| E5 | 上游合并、他人使用或引用 | 有外部证明 |

核心技能至少达到 E3；专业化方向至少两个主题达到 E4；最终至少一个成果达到 E5。

## 6. 24 个月路线图

## 阶段 0：基线与环境（第 1–2 周）

目标：建立可靠学习环境和当前能力基线。

任务：

- 安装并熟悉 WSL2/Ubuntu、Git、uv、Python、Docker；
- 建立虚拟环境、pytest、ruff、mypy/pyright 和 pre-commit；
- 为当前 LangGraph Demo 增加最小测试、配置和错误处理；
- 记录电脑 CPU、内存、GPU、显存、驱动和 CUDA 能力；
- 运行一次本地或云端小模型推理；
- 完成本计划的第一份周报。

交付物：

- `environment.md`：硬件、系统、版本、安装步骤；
- `baseline.md`：Python、Linux、PyTorch、网络、数学自测；
- 至少 5 个可重复测试；
- 一条从请求到模型响应的追踪日志。

通过门槛：换一台机器时，可以只看文档在 2 小时内重建环境。

## 阶段 1：工程与模型基础（第 3–8 周）

目标：补齐阅读推理系统源码所需的最低基础。

必须掌握：

- Python：迭代器、生成器、装饰器、类型、dataclass/msgspec、asyncio、multiprocessing；
- 系统：进程/线程、IPC、socket、HTTP/SSE、序列化、日志；
- PyTorch：Tensor、device、dtype、autograd 基础、module、显存；
- Transformer：tokenization、embedding、attention、RoPE、MLP、采样；
- 推理：prefill、decode、KV cache、batching、quantization；
- 测试：unit、integration、benchmark、随机性控制。

项目 P1：`mini-inference-lab`

- 用 PyTorch 实现一个极小 Transformer 的推理路径；
- 显示 prefill/decode 两阶段耗时；
- 实现最简单 KV cache，并与无 cache 版本对比；
- 添加 greedy、temperature、top-k/top-p；
- 记录不同序列长度的时间和内存；
- 至少 20 个测试。

通过门槛：

- 能画出一次请求的数据流；
- 能解释为什么 decode 通常更受内存带宽影响；
- 能用实验展示 KV cache 的收益与代价；
- 项目在干净环境可复现。

## 阶段 2：Serving 与基准系统（第 9–16 周）

目标：从“模型推理”进入“多请求推理系统”。

主题：

- continuous batching；
- TTFT、TPOT、ITL、吞吐和 goodput；
- 静态/动态 batch；
- 请求长度分布与并发；
- PagedAttention/RadixAttention；
- prefix caching；
- structured output；
- OpenAI-compatible serving；
- tracing、metrics、load generation。

项目 P2：`serving-benchmark`

- 同一模型、同一机器运行 Transformers、vLLM、SGLang；
- 至少设计三种 workload：短对话、长上下文、共享前缀 Agent；
- 控制 warmup、seed、请求到达率、输入/输出长度；
- 输出 P50/P95/P99 TTFT、TPOT、吞吐、错误率、峰值显存；
- 保存环境、commit SHA、命令、原始 JSON/CSV；
- 分析结果而不是只画柱状图。

通过门槛：第三个人能按 README 重现实验；所有结论都能追溯到原始数据；能解释两个引擎差异的至少两个源码依据。

## 阶段 3：SGLang 源码与第一次贡献（第 5–8 月）

目标：从使用者变成能修改和验证的贡献者。

源码阅读顺序：

1. HTTP 入口与参数；
2. tokenizer/请求管理；
3. scheduler 主循环；
4. batch 构造和 forward 路径；
5. KV cache 与 Radix cache；
6. model runner；
7. sampling/structured output；
8. router、metrics 和 tests；
9. 再进入 kernel、并行或 disaggregation。

每次源码阅读必须产出：

- 一张调用链图；
- 一个最小可运行实验；
- 一个“不变量/失败条件”列表；
- 至少一个断点或日志观察；
- 一个可以写成测试的问题。

贡献阶梯：

1. 修正文档或可复现命令；
2. 补充 CPU unit test；
3. 修复测试或错误信息；
4. 修复一个有回归测试的小 bug；
5. 做一次测量充分的小型性能优化；
6. 再尝试 scheduler/cache/model/kernel 级功能。

通过门槛：

- 至少提交 2 个有效 PR，其中至少 1 个被合并或获得维护者实质性 review；
- 能独立运行相关 unit test 和最小 E2E；
- 能说明修改涉及的正确性、性能和兼容性风险。

## 阶段 4：实时多模态系统（第 9–12 月）

目标：把推理知识转化为用户可感知价值。

项目 P3：`realtime-domain-agent`

推荐主题：中文实时学习/研究助手。不要做泛化“万能助手”。

最低能力：

- LiveKit Agents 实时语音；
- 本地/自托管推理服务；
- 流式 STT、LLM、TTS 或 speech-to-speech；
- 用户打断与 barge-in；
- 工具调用和会话状态；
- tracing、日志和指标；
- 限流、超时、重试和降级；
- 离线回放评测；
- 隐私和敏感数据边界。

核心指标：

- 用户说完到系统开始响应的延迟；
- 打断成功率和误打断率；
- 工具调用成功率；
- 任务完成率；
- 每分钟会话成本；
- 异常恢复时间；
- 端到端 P95 延迟。

通过门槛：至少 5 名真实测试者、50 次完整会话、公开失败案例和下一版决策。没有真实用户反馈不得宣称“生产级”。

## 阶段 5：生产可靠性（第 13–15 月）

目标：证明系统不仅能演示，而且能被测量、恢复和安全降级。

项目 P4：`inference-reliability-lab`

必须覆盖：

- SLI/SLO/error budget；
- metrics、logs、traces；
- admission control、backpressure；
- timeout、retry、circuit breaker；
- canary 与性能回归门禁；
- GPU OOM、worker 崩溃、网络抖动；
- 请求取消、重复工具调用、非法结构输出；
- 模型、STT、TTS 依赖故障；
- blameless postmortem。

通过门槛：

- 服务连续运行 7 天；
- 人工注入至少 10 类故障；
- 核心单实例故障能够恢复或明确降级；
- 无已知重复副作用；
- 每类故障可由 trace 定位；
- 性能/正确性回归超过阈值可自动阻断合并。

## 阶段 6：专业化（第 16–18 月）

在以下方向中只选一个主方向、一个辅方向。

### A. 调度与 KV Cache

- 共享前缀 Agent workload；
- cache-aware routing；
- eviction 和 fragmentation；
- prefill/decode 调度；
- goodput 与 SLO；
- 长上下文和多轮会话。

### B. GPU Kernel 与量化

- roofline 思维；
- torch.profiler、Nsight Systems/Compute；
- Triton；
- attention、GEMM、MoE；
- FP8/FP4/INT4；
- CUDA Graph 和 torch.compile。

### C. 分布式与异构硬件

- tensor/pipeline/expert/data parallel；
- NCCL/RDMA/NIXL；
- PD disaggregation；
- 多节点故障；
- NVIDIA/AMD/TPU/NPU 差异。

### D. RL Rollout 与确定性

- rollout throughput；
- 权重热更新；
- training–inference mismatch；
- logprob 一致性；
- deterministic inference；
- 多轮/多模态 RL workload。

### E. 实时语音/多模态可靠性

- 音频输入输出队列；
- 端到端与级联模型；
- jitter、背压和取消；
- 质量/延迟联合评测；
- 多模态上下文和缓存。

选择标准：兴趣 25%、现有资源 20%、公开问题数量 20%、产业需求 20%、可形成独有数据 15%。不得因为某方向“听起来最底层”就盲选。

阶段交付：

- 一份 5,000 字以上技术报告；
- 一个有基线、实验、消融和失败分析的实现；
- 一次公开分享；
- 至少一个高质量上游 Issue/PR；
- 一套领域 workload 或评测集。

## 阶段 7：原创价值与外部验证（第 19–24 月）

目标：从“学习已有系统”转向“提出并验证自己的问题”。

最终项目必须满足：

- 问题来自真实用户或真实部署，而非凭空想象；
- 至少两个公开基线；
- 明确正确性与性能指标；
- 有消融实验；
- 有失败结果；
- 有可复现代码和数据说明；
- 至少一种外部验证：上游合并、用户采用、公开引用、演讲或第三方复现。

候选课题示例：

- 面向多轮工具调用 Agent 的共享前缀 workload 与 cache-aware 调度；
- 中文实时语音 Agent 的延迟—质量联合基准；
- 推理引擎在取消、重试和工具调用下的 KV cache 生命周期；
- 多租户 Agent 的 SLO-aware 调度与公平性；
- RL rollout 的确定性与 logprob 漂移诊断；
- 国产硬件上的模型兼容性和性能回归系统。

## 7. 前 12 周逐周计划

| 周 | 唯一主目标 | 必须交付 |
| --- | --- | --- |
| 1 | 环境与能力基线 | 环境文档、硬件清单、自测结果 |
| 2 | 当前 Agent 工程化 | 配置、测试、日志、README |
| 3 | Tensor/dtype/device | 10 个小实验与笔记 |
| 4 | Transformer 推理路径 | 手绘数据流、最小实现 |
| 5 | Prefill/decode 与 KV cache | 有/无 cache 对照实验 |
| 6 | asyncio、SSE、并发 | 流式服务与并发测试 |
| 7 | 采样与结构化输出 | 测试覆盖边界条件 |
| 8 | 阶段复盘 | P1 发布、缺口清单 |
| 9 | 启动三个 serving 后端 | 可复现启动脚本 |
| 10 | 负载发生器与指标 | 原始数据 schema、单测 |
| 11 | 短/长/共享前缀实验 | 第一版 benchmark 数据 |
| 12 | 公平性和误差审计 | 报告 v0.1、下一轮假设 |

详细执行时，始终只提前规划未来两周；12 周表是路线，不是不可改变的日历。

## 8. 硬件分层方案

### H0：没有 NVIDIA GPU

- 做 CPU unit tests、请求层、router、parser、metrics、文档；
- 使用小模型、模拟器和云端短租 GPU；
- 研究 workload、正确性、评测和实时系统；
- 每月集中租用 8–16 小时 GPU 完成实验，不长期空转。

### H1：8–16GB 消费级 GPU

- 小模型 serving、量化、单 GPU profile；
- 重点研究请求调度、缓存、结构化输出和实时系统；
- 不勉强复现多节点结论。

### H2：24GB 以上单卡或可短租多卡

- 进行并发、量化、长上下文和小规模并行实验；
- 使用固定预算和自动关机；
- 每次租用前先在 H0/H1 完成脚本与 dry run。

### H3：多节点高端 GPU

只有在明确研究问题、已有基线且能获得资源时使用。硬件规模不是学习成果。

## 9. 作品集标准

最终作品集至少包含：

1. 一个从零实现的最小推理实验室；
2. 一个可复现的 vLLM/SGLang benchmark；
3. 一个真实实时多模态 Agent；
4. 一个专业化研究项目；
5. 至少两个上游贡献；
6. 四篇深度文章；
7. 一次公开分享；
8. 一个领域数据集、评测集或长期 workload；
9. 失败实验档案；
10. 能说明个人贡献范围的项目文档。

每个作品必须回答：问题是什么、为什么重要、基线是什么、如何测量、结果是否可信、失败在哪里、下一步是什么。

## 10. 量化记分卡

每月记录：

| 指标 | 月度目标 | 警戒线 |
| --- | ---: | ---: |
| 深度工作小时 | 60 | < 32 |
| 可复现实验 | 4 | < 2 |
| 自动化测试新增 | 10 | < 4 |
| 源码调用链笔记 | 2 | 0 |
| 公开输出 | 1 | 连续 2 月为 0 |
| 上游互动 | 2 | 连续 2 月为 0 |
| 失败实验记录 | ≥1 | 0（可能在回避困难） |
| 真实用户会话（阶段4起） | 20 | <5 |

禁止用 commit 数、代码行数和 Star 数替代真实成果。

## 11. 月度与季度维护

### 每周

- 使用 `templates/WEEKLY_REVIEW.md`；
- 记录计划时间和实际时间；
- 只保留一个下周主目标；
- 把失败实验加入档案；
- 更新当前证据等级。

### 每月

- 汇总记分卡；
- 删除低价值课程和重复资料；
- 选择下月唯一能力主题；
- 检查支出、GPU 成本和时间；
- 发布一个可供他人检查的成果。

### 每季度

- 使用 `templates/QUARTERLY_REVIEW.md`；
- 重新扫描 SGLang/vLLM 路线图和近期提交；
- 检查主项目是否仍活跃、开放、被采用；
- 重新评分五个候选方向；
- 决定继续、调整专业化或迁移载体；
- 将本文件版本提升为 v1.1、v1.2 或 v2.0，并写变更原因。

## 12. 止损与转向规则

出现任意一项，不应继续盲目 All-in：

- SGLang 连续两个季度核心活动明显下降；
- 核心设计封闭化，外部贡献无法进入；
- 主流模型/硬件长期无法支持；
- 生产采用明显迁移到其他开源运行时；
- 个人经过两个季度仍强烈厌恶系统调试和性能测量；
- 无法稳定获得最低实验资源；
- 12 个月仍只有教程 Demo，没有外部反馈或贡献。

转向时保留技能主线，替换载体：

- SGLang → vLLM、TensorRT-LLM、llama.cpp 或新运行时；
- 语音 Agent → 多模态/视频/边缘推理；
- GPU kernel → 调度、评测、可靠性或异构硬件；
- 纯软件推理 → LeRobot 具身智能。

## 13. 风险登记

| 风险 | 概率 | 影响 | 应对 |
| --- | --- | --- | --- |
| 只追新技术、不完成项目 | 高 | 高 | 每季度最多一个新主项目 |
| GPU 资源不足 | 中 | 中 | H0/H1 路线、集中短租、先 dry run |
| 基础薄弱导致源码挫败 | 高 | 中 | 前 8 周补基础，按调用链读 |
| 只做应用层 | 高 | 高 | 每周固定源码与测量时间 |
| 只做底层、没有用户价值 | 中 | 高 | 第 9–12 月强制真实用户测试 |
| AI 自动生成掩盖理解不足 | 高 | 高 | 要求口头解释、手工实验和失败分析 |
| 单一项目衰落 | 中 | 中 | vLLM 对照、季度生态复审 |
| 课程收集成瘾 | 高 | 中 | 新资源必须替换旧资源，不得只增加 |
| 输出过少、没有外部证明 | 中 | 高 | 每月至少一个公开成果 |

## 14. 使用 AI 助手的规则

允许 AI：

- 解释源码、生成测试草案、提出实验矩阵；
- 审查 benchmark 公平性；
- 帮助定位日志和搜索上游问题；
- 将周报整理成文档；
- 扮演反方审查结论。

不允许 AI 替代：

- 你对结果的解释；
- 原始数据和真实运行；
- 对 PR 改动范围的理解；
- 对安全、成本和用户风险的判断；
- 学习证据。

任何 AI 生成代码进入作品前，你必须能解释数据流、失败模式和测试覆盖。

## 15. 立即执行的下一步

1. 完成 `templates/BASELINE.md`；
2. 记录每周可稳定投入的时间，而不是理想时间；
3. 完成阶段 0 的环境清单；
4. 把当前 LangGraph 学习助手整理为可测试项目；
5. 建立第一份周报；
6. 第 2 周结束后再决定是否需要购买或租用 GPU。

本计划的第一原则是：**用可验证的作品代替“学过”，用持续的外部反馈代替自我感觉，用可迁移的底层能力对冲单一项目风险。**
