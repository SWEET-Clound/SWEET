# Week 7 逐次执行手册：trace、重试与故障注入

> 核心 10h + 深化 5h。目标是让一次请求在失败时可被解释；不是部署完整观测平台。

## 知识地图

| 概念 | 最小可验证定义 | 反例 |
| --- | --- | --- |
| trace/span | 共享 trace_id 的因果步骤记录 | 只有散乱日志行 |
| retry | 仅对允许的临时失败、受 deadline 限制 | 任何异常无限重试 |
| idempotency | 重放不会产生额外副作用 | 对写操作盲目 retry |
| fault injection | 可控 fake 在指定点失败 | 拔网线猜测行为 |
| error taxonomy | 错误类别稳定、细节受限 | 直接暴露 provider traceback |

## Session 1（2h）：设计最小 trace schema

创建 `TraceRecorder`（或严格限定的 OpenTelemetry SDK，二选一，默认前者）。每条 JSONL span 至少有：`trace_id, span_id, parent_span_id, name, started_ns, ended_ns, outcome, error_code, attempt`。

1. 先写测试：一次 success 有 `http.request → service.run → dependency.call` 的父子关系；时长非负。
2. 使用注入的 clock/ID factory，使测试不依赖真实时间。
3. 不记录 prompt、Key、authorization；记录长度、类别与 hash（如确有必要）。

## Session 2（2h）：错误分类和可测 fake

1. 定义 `InvalidInput`, `DependencyUnavailable`, `DependencyTimeout`, `InternalError`；写 API 映射表。
2. fake dependency 支持 `success`, `timeout`, `transient_503`, `permanent_4xx`, `side_effect_started`。
3. 每种场景先断言 HTTP/stream terminal event，再断言 trace outcome 与无秘密字段。

## Session 3（2h）：重试 contract

明确 allowlist：本周只重试 `DependencyTimeout` 与 `transient_503`；最大 attempts=2，固定/可注入 backoff，受总 deadline 约束。`InvalidInput`、permanent 4xx 和潜在副作用操作不得重试。

测试矩阵：首次成功（1 attempt）、一次 transient 后成功（2）、持续 transient（2 后失败）、永久错误（1）、deadline 先到（失败且次数有限）。每个测试检查 span 的 `attempt`。

## Session 4（2h）：故障注入与定位

1. 运行至少 4 个故障情景；每个保存 JSONL trace 与对应测试名。
2. 练习从 trace 回答：失败在哪一层？是否 retry？为何停止？调用者看到了什么？
3. 失败分支：trace 不完整→把 span close 放入 finally；重试次数多→检查 retry 是否包裹整个 request，而非 dependency call。

## Session 5（2h）：失败标签资产与复盘

1. 在 `workloads/agent_cache_lab/failure_taxonomy.md` 定义 label、触发条件、用户可见行为、对应测试/trace。
2. 写 `experiments/week_07/fault_report.md`，每例按“假设→注入→raw trace→观察→局限”组织。
3. 跑质量门与 [SGLang Lab](SGLANG_LAB.md)。

## 深化

可替换 TraceRecorder 为 OTel exporter，但必须保持同一测试 contract；不能因为接入工具而省略 fault matrix。完成需 4 类故障、retry allowlist、trace 父子关系测试、failure taxonomy。
