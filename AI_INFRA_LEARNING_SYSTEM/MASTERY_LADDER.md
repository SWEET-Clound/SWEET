# 从 Week 1 到“能掌握项目”的验证阶梯

“掌握整个项目”不是把文档读完，而是能在版本变化、真实负载和外部审查下持续做正确判断。以下阶梯把长期目标变成可证伪的里程碑；没有跳级。

## Level 0：学习环境可信（Week 1–2）

**证明**：项目根目录 venv 能运行；pytest 能离线通过；配置、secret、安全表达式与测试思维有真实命令证据。

**不能声称**：会工程化、会异步或理解 SGLang。

## Level 1：Agent 工程与源码入口（Week 3–8）

**证明**：可测试 API/stream/cancel/queue/trace；干净环境可重建；synthetic/脱敏 workload 带数据卡；SGLang 固定 SHA、最小测试、调用图和一个未复现 issue 卡。

**外部检验**：让另一人按 README 重建；她能重放 JSONL 并找到证据索引。

**不能声称**：能调度 GPU，或已复现上游 issue。

## Level 2：模型与 cache 正确性（Week 9–12）

**证明**：Tensor/attention/decoder/local cache 有 CPU FP32 oracle、反例、原始 CSV 和 profiler；每 token 的 cache/no-cache logits 一致；能清楚划分 local cache 与 prefix/page/session cache。

**外部检验**：第三方用同 revision/环境运行 tests 并从 raw 数据重画两张图。

**不能声称**：真实服务 TTFT/throughput 更快，或已掌握 RadixAttention/PagedAttention。

## Level 3：真实 serving 比较（Week 13–16）

**证明**：同模型/机器/workload 下，Transformers、SGLang、vLLM 的公平 benchmark；包含 warmup、seed、到达模型、P50/P95/P99、错误率、OOM、raw JSONL/CSV、硬件与 commit SHA。

**外部检验**：第三方可以复跑一个小矩阵，得出方向一致的结论；若不一致，能够解释版本/硬件/负载差异。

## Level 4：源码问题到有效贡献（Month 5–8）

**证明**：一个狭窄问题从 trace/workload/测试到最小复现，再到修复或测试 PR；理解它的正确性、兼容性、性能和回归风险。

**外部检验**：维护者给出实质性 review 或合并；未合并也要保留 review 意见与修订记录。一个纯文档拼写 PR 不能替代此目标。

## Level 5：不可轻易复制的资产（Month 9–18）

**证明**：持续积累的 Agent/RL session/shared-prefix/cancel workload、脱敏规则、标签、指标、回放器和跨引擎报告；或真实产品中可复现的性能/可靠性问题。

**外部检验**：他人使用 workload、引用报告、复现问题，或真实团队采用其中一个方法。

## Level 6：项目级掌握（18–24+ months）

达到以下多数条件时，才能谨慎说“掌握该项目的一个专业方向”：

- 能在新 release 中定位 API/调用链变化并更新测试；
- 能解释某个 scheduler/cache/serving 结论的适用负载、硬件和失败条件；
- 有被外部 review 的修复、实验或设计；
- 能将方法迁移到 vLLM/其他 runtime，证明不是死记单仓库；
- 有真实 workload 或用户问题让你提出的新假设难以被教程复制。

## 每月问题（防止自我欺骗）

1. 我本月的结论能指向哪份 raw data、哪个 revision、哪条命令？
2. 哪个反例可能推翻它？我实际测过吗？
3. 我得到的是“会调用 API”，还是“可以定位一个失败并设计测试”？
4. 我的 workload/trace 是否合规、脱敏、可删除，而且明确了它不能代表什么？
5. 有无外部人、真实用户或维护者检查过？没有时下一步怎样获得检查？

这份阶梯不承诺时间必然换来掌握；它承诺不把“看过、跑过、AI 生成过”伪装成掌握。
