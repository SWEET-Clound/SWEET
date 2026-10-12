# 前 12 周路线一致性审计

审核日期：2026-10-04。该文件回答三个问题：计划有没有偏离目标？每周是否有可验证练习？12 周结束时到底能诚实地声称什么？

## 与既定方向的对齐

既定主线不是“会调用 SGLang API”，而是 **Agent/RL workload 下的 KV Cache、调度、路由和正确性**，并以 SGLang 为主实验场、vLLM 为对照、LiveKit 为后续产品入口。

| 能力链 | 负责周 | 形成的可复用资产 |
| --- | --- | --- |
| 可维护 Agent 与测试边界 | 1–4 | offline tests、模块契约、API、request ID |
| 生命周期与压力理解 | 5–7 | cancellation trace、queue/admission policy、负载原始数据、failure taxonomy |
| 证据与方向选择 | 8 | 复现报告、工程 Gate |
| 模型计算与数值正确性 | 9–11 | tensor experiments、attention reference、decoder profiler baseline |
| cache / 调度问题的起点 | 12 | cache correctness suite、latency-memory report、下阶段问题清单 |

这条顺序没有提前把用户投入到框架配置或大模型下载，而是先形成能解释、测量、反驳结果的能力。这正是“独一无二价值”所需的第一批资产：可迁移实验方法、可信 workload、故障分类、正确性测试和公开报告。

## 练习与证据审计

| 周 | 每周主练习 | 必须留下的证据 | 不允许的替代 |
| ---: | --- | --- | --- |
| 1 | 环境 / 测试 / trace | 环境输出、5+ 行为测试、失败表 | 截图或“安装成功” |
| 2 | 配置 / AST calculator | 10+ 离线测试、无 `eval`、复现说明 | 真实 Key 调用 |
| 3 | 模块边界 / 质量门 | ADR、20+ 测试、一条质量命令 | 只移动文件 |
| 4 | HTTP / request ID | schema tests、health、结构化日志 | 手工浏览器试一次 |
| 5 | cancel / streaming | task cleanup tests、事件契约 | async 语法背诵 |
| 6 | concurrency / backpressure | 1/5/10/20 × 5 raw data | 一个平均吞吐数字 |
| 7 | tracing / fault injection | 5 类 trace、retry ADR、修复证据 | 只有日志或 mock 全局对象 |
| 8 | Gate 0A | 30 分钟运行、干净复现、2,000 字复盘 | 新功能 |
| 9 | tensor/dtype/device | 10 个实验、raw CSV、profile trace | 单次 GPU 计时 |
| 10 | causal attention | reference-vs-SDPA tests、shape 图 | 复制 attention 模块 |
| 11 | decoder/generate | prefill/decode 基线、profiler | 只跑预训练模型 |
| 12 | KV cache | cache/no-cache 对照、5 次矩阵、报告 | 背诵 KV Cache |

## 对“掌握开源项目”的诚实定义

12 周不能诚实保证“完全掌握 SGLang”——它是持续演进、含 runtime、scheduler、GPU kernel、分布式、模型适配的大型项目。任何承诺只用 180 小时就完全掌握它都是不可靠的。

本计划的 12 周可证明目标是：

1. 你能够独立建立和测试一个 Agent 服务的正确性、并发、故障和可观测性；
2. 你能从 Tensor 到 KV Cache 自己实现并验证关键计算；
3. 你拥有进入 SGLang/vLLM 源码、跑小模型对照 benchmark、识别 cache/scheduler 问题的共同语言与实验工具；
4. 你能通过 Gate 0A/0B 决定是否继续投入，而不是因已经投入时间而盲目加码。

之后的真正“项目掌握”在第 13 周起以可审查源码阅读、双后端 benchmark、issue/PR、真实 workload 和外部反馈来证明；它不能由学习时长替代。

## 12 周结束后的独特价值起点

```text
可信工程 + 正确性实验 + 负载/trace 数据
                     ↓
Agent/RL session workload 评测基线
                     ↓
SGLang/vLLM 跨后端 cache/scheduler 比较
                     ↓
可公开复现的报告、issue、PR 或垂直场景工具
```

“独一无二”不会来自某个练习答案，而来自持续积累的、他人难以立即复制的 workload、trace、评测标准和跨层解释。这 12 周的所有产物都必须保留，以便成为那条资产链的第一版。

## 完成审计规则

- 每周未达到 DoD，延长当前周，不把半成品推进；
- 每个性能结论保留版本、硬件、命令、原始数据和重复次数；
- 每个正确性结论说明 oracle、容差和未覆盖情况；
- 每个外部依赖测试可区分 fake 与真实集成测试；
- Gate 未通过时允许 pivot；这比虚假的“按计划完成”更有价值。
