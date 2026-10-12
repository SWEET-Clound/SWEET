# Week 6：并发、背压与负载实验

> 10 小时核心 + 5 小时深化。主问题：**负载增长时，系统是可预测地排队/拒绝，还是静默积压到失控？**

按 [START_HERE.md](START_HERE.md) 建指标，资料只读 [RESOURCES.md](RESOURCES.md) 指定部分；每轮负载实验必须先填 [EXERCISES.md](EXERCISES.md) 的假设，再记录进 [WORK_LOG.md](WORK_LOG.md)，最终按 [RUBRIC.md](RUBRIC.md) 判断。

## 本周的系统观

```text
arrival rate → admission semaphore → bounded queue → worker → response
                           │                  │
                           └── reject/timeout ┴── metrics
```

本周不追求高吞吐数字；先建立可信的负载实验与退化策略。它是未来理解 continuous batching、scheduler 与 KV cache admission 的直接前置能力。

## 五个学习板块

### W6-1：排队直觉与指标定义（2.5 小时）

**知识：** 并发数不是吞吐；吞吐是单位时间完成数；latency 是单请求耗时；P50/P95/P99 描述分位尾部；错误率必须与样本数一起报告。系统稳定需要服务能力长期不低于到达压力。

**路径：**

1. 在 `experiments/week_06/metrics.md` 定义：start、admit、service_start、finish、reject 时间点；
2. 明确报告：请求数、成功/拒绝/超时、总时长、throughput、P50/P95、最大 queue depth；
3. 手工对 10 个延迟样本排序，计算 P50/P95，写下“小样本 P95 不稳定”的限制；
4. 写 `summarize_latencies(samples)` 纯函数与空样本、单样本、重复值测试；
5. 将原始每请求记录保存 JSONL，摘要绝不替代原始数据。

**资源：** [Python statistics](https://docs.python.org/3/library/statistics.html)，只读 mean、median、quantiles 与空数据行为。

**产出 / 验收：** 指标字典、纯统计函数及 6+ 测试。没有“仅给一个平均值”的报告。

### W6-2：并发限制与 admission（3 小时）

**知识：** `asyncio.Semaphore(n)` 限制同时进入服务的工作数；它不是队列，也不等于公平调度。admission 必须明确“等待多久、满时怎么办”。

**路径：**

1. 使用 deterministic fake model：每次 sleep 固定/可控时长；
2. 首先无 semaphore 跑 10 请求，观察 in-flight 峰值；
3. 加 `Semaphore(2)`，记录 in-flight 最大值绝不超过 2；
4. 定义 acquire timeout；等待超时返回明确 overload 错误；
5. 写测试：并发上限、拒绝、release 后后续请求可进入、异常时 permit 不泄漏。

**练习：** 画 5 个请求、2 个 permit 的时间线；预测哪两个先服务，何时第 3 个开始。

**产出 / 验收：** admission policy 文档和 5+ 并发行为测试。

### W6-3：有界队列与背压策略（3 小时）

**知识：** 无界队列把过载变成内存/等待时间问题；有界队列迫使系统在等待、拒绝、丢弃之间选择。本周选择“短等待后拒绝”，因为 Agent 请求不能静默丢失。

**路径：**

1. 建 `Queue(maxsize=4)` 与固定数量 worker；
2. 定义 message：request_id、deadline、payload、future；
3. queue put 也有 deadline；满时返回 429/503 之一，并写下选择理由；
4. 测试：队列峰值不超过 4、满时可观测拒绝、worker 异常会把错误传回对应 future、shutdown 会停止 worker；
5. 记录 queue waiting time 与 service time，避免混为一个 latency。

**练习：** 改变 queue size 为 1、4、20，预测对 P95、拒绝率、内存风险的方向影响，再用 fake workload 验证。

**产出：** `backpressure_policy.md`、队列 lifecycle trace、6+ 测试。

### W6-4：负载矩阵与可复现实验（3.5 小时）

**路径：**

1. 固定环境、fake 服务延迟、并发限制和队列大小；
2. 跑 1 / 5 / 10 / 20 客户端并发，每档至少重复 5 次；
3. 每次保存原始 JSONL，聚合成 CSV；
4. 写图或表：吞吐、P50/P95、错误率、max queue depth；
5. 仅基于数据写三段解释：饱和点、背压是否按 policy 工作、结果的局限。

**练习：** 先写假设：“并发超过 worker 能力时，P95 上升且拒绝出现，而 in-flight 与 queue depth 有上界。”只有数据支持时才保留。

**产出 / 验收：** `experiments/week_06/raw/`、`summary.csv`、`report.md`。每个图表可追溯到原始数据和命令。

### W6-5：从 Agent 背压到推理 scheduler（3 小时）

**知识连接：** 这里的 permit、queue、deadline、admission 与 SGLang/vLLM 的连续批处理、请求队列、prefill/decode 资源竞争在概念上同构，但本周没有声称实现了 GPU scheduler。

**路径：**

1. 阅读 SGLang benchmark 输出的 TTFT/ITL/TPOT 定义；
2. 将自己的 time points 映射到“等待 / 服务 / 完成”；
3. 写一张映射表：本周 queue 与将来 token scheduler 相同/不同处；
4. 注入慢请求，观察 head-of-line blocking；
5. 写一个 Week 7 tracing 必须回答的问题。

**本周 DoD：** 1/5/10/20 并发、每档 5 次、有原始数据；过载时不失控；能解释饱和点而不是报一个最大 TPS。总测试建议 50+。
