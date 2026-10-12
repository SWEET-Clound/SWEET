# Week 6 逐次执行手册：并发、背压与可复现负载

> 核心 10h + 深化 5h。目标是说明“系统在固定压力下如何排队、拒绝或超时”，不是刷高并发数字。

## 知识地图

| 概念 | 本周可测定义 | 反例 |
| --- | --- | --- |
| 并发上限 | 同时在运行的请求不超过 N | 只看创建 task 数 |
| queue/backpressure | 排队长度和拒绝规则可观察 | 无限制积压直到 OOM |
| overload 状态 | 本周固定选择 429 或 503 并解释 | 随机返回 500 |
| latency percentile | 排序后固定分位算法 | 只报告最快一次 |
| deterministic workload | 相同 seed 得同一 JSONL | 每次随机 prompt/到达时间 |

## Session 1（2h）：先定义 overload 决策

1. 写 `docs/week_06_capacity_contract.md`：`max_in_flight`、`max_queue`、服务时间 fake、超出时本周统一返回的状态（建议 429 表示暂时拒绝；若选 503 写理由）。
2. 用 `asyncio.Semaphore` 或显式 queue 实现最小 admission gate；不要用全局 `all_tasks`。
3. 测试最大并发：用 barrier 让 N 个 worker 同时进入，断言 active peak 不超过 N。

## Session 2（2h）：排队、取消与 shutdown

1. 为 queue 满、请求取消、worker exception、shutdown pending futures 各写一个测试。
2. 每个 future 必须在 terminal 状态被 resolve/cancel；写 `assert no_pending_requests` 的 teardown。
3. 失败分支：偶发测试 → 用 Event/barrier，不使用任意 `sleep(0.1)`；pending → 记录 task name/registry ID，而不是强杀全部 task。

## Session 3（2h）：负载 JSONL 与统计函数

定义单行 schema：

```json
{"request_id":"r0001","arrival_ms":0,"input_tokens":16,"output_tokens":8,"session":"s01","prefix_group":"pA","cancel_at_ms":null}
```

1. 写 generator `generate_workload(seed, count)`，固定 seed 相同字节输出；每行不含真实文本或身份。
2. 写 `quantile_nearest_rank(values, p)` 或明确定义线性插值，测试空数组、单元素、乱序和 P95。
3. 输出 raw result JSONL：每请求 start/end/queue/service/status/error 类别。

## Session 4（2h）：受控实验

1. 固定矩阵：`max_in_flight=1/2/4` × arrival `slow/burst` × 3 repeats；fake service 延迟固定。
2. 每次记录 configuration、seed、每个请求 raw result；汇总 P50/P95、accepted/rejected/cancelled、peak queue。
3. 不能从模拟器数字声称真实模型吞吐；报告只讨论队列策略的逻辑影响。

## Session 5（2h）：解释与资产

1. 写 `experiments/week_06/backpressure_report.md`：假设、算法、raw data、结果、反例、限制。
2. 将 shared-prefix/multi-turn JSONL 放入 `workloads/agent_cache_lab/`，写数据字段说明和 seed。
3. 跑所有测试 + [SGLang Lab](SGLANG_LAB.md)。

## 深化

比较 reject vs bounded-wait 两个明确策略，保持同一 workload；不要混入真实 GPU/model 下载。完成需：并发峰值证据、queue 满测试、确定性 JSONL、统计函数测试、报告。
