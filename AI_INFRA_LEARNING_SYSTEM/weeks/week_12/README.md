# Week 12：KV Cache、实验报告与 Gate 0B

> 10 小时核心 + 5 小时深化。主问题：**KV Cache 何时节省计算、付出多少显存、何时不值得；能否用重复实验而不是背定义回答？**

按 [START_HERE.md](START_HERE.md) 先写不变量；资料在 [RESOURCES.md](RESOURCES.md)，对照题和 benchmark 设计在 [EXERCISES.md](EXERCISES.md)，原始数据和口试答案写入 [WORK_LOG.md](WORK_LOG.md)，最终 Gate 使用 [RUBRIC.md](RUBRIC.md)。

## 五个学习板块

### W12-1：先写 KV Cache 不变量（2.5 小时）

**知识：** 对每层每个已处理 token 缓存 K/V。第 t 步新 Q 仍需对所有历史 K/V attention；cache 避免重算历史 K/V，不让 attention 立即变成 O(1)。

**不变量：** cache 长度随已处理 token 增加；batch/head/dtype/device 与当前输入一致；eval/dropout=0 时 cache/no-cache logits 在容差内相等；不跨独立会话复用。

**练习：** 对 `B,H,T,D` 推导每层 K+V 理论字节：`2*B*H*T*D*bytes_per_element`，再乘层数。预测 T 翻倍结果。

### W12-2：两条生成路径（3.5 小时）

1. 保留 Week 11 no-cache reference；
2. 定义明确 cache 数据结构（每层 K,V，禁止隐式全局变量）；
3. prefill 返回初始 cache；每次 decode 只投影新 token 并 append；
4. 为 T=1、短 prompt、不同 batch、EOS、最大长度写对照测试；
5. 有任何不一致先最小化到单层/单头/FP32 CPU，再排查 mask、position、concat 维度；不得先放宽容差。

**产出：** cache state shape 表与 10+ cache correctness tests。

### W12-3：公平 benchmark 设计（3 小时）

**控制变量：** 相同权重、prompt、输出长度、seed、device、dtype、warmup、重复数；分别测 no-cache 和 cache。

**矩阵：** context `16/64/256/512` × generated `1/16/64`；每个点至少 5 次；无法放入内存的点记录 OOM 而不是删掉。

**指标：** prefill、TTFT、decode TPOT、total time、peak memory、输出一致性、变异。不要把 prefill 和 decode 合成一个数字掩盖 trade-off。

### W12-4：分析、反例与 SGLang 映射（3.5 小时）

1. 输出 raw CSV 与汇总表；
2. 画 context 变化下 TPOT/显存图；
3. 至少找一个 cache 收益不明显或受限的情形，并解释可能原因；
4. 映射到 serving：单请求 local cache 与多会话 prefix/session cache 的区别；
5. 写“为什么这不是 SGLang RadixAttention/PagedAttention benchmark”的边界；
6. 阅读 SGLang/vLLM 对 KV cache 的高层文档，列出后续需验证的问题：block allocation、prefix sharing、eviction、admission、multi-tenant correctness。

### W12-5：季度 Gate 0B（2.5 小时）

提交 `experiments/week_12/kv_cache_report.md`，必须包含：环境、代码 revision、假设、方法、原始数据位置、重复次数、统计、图表、正确性结果、失败点、局限、下一步。

最终口试：

1. 为什么 cache/no-cache 必须输出一致？
2. cache 减少哪种重复计算，未消除哪种计算？
3. 为什么 cache 会增加显存？
4. TTFT、TPOT、total time 各看什么？
5. 从单请求 cache 到 Agent session/prefix cache 增加了哪些正确性与调度问题？

**Gate 0B DoD：** 两路径在定义容差内一致；矩阵每点 5 次；原始数据和限制完整；能用数据解释收益/代价。通过后才开始 Week 13+ 的 SGLang/vLLM 双后端 serving benchmark；未通过先修正确性或测量，不急于安装更大模型。
