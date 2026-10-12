# Week 5 逐次执行手册：async、SSE、取消与资源生命周期

> 核心 10h + 深化 5h。目标是可取消、可测试的本地流式协议，而不是并发量很高的生产系统。

## 进入 Gate 与知识地图

先跑所有 API tests。若 Week 4 未完成，继续使用 fake service，不连接真实 LLM。

| 概念 | 必须证明的事 | 常见误解 |
| --- | --- | --- |
| coroutine/task | 调度单位与执行结果可区分 | `async def` 自动并发 |
| timeout | 超时边界在调用者处明确 | 捕获 `CancelledError` 后吞掉 |
| SSE | 每事件有 framing、event/data、终止语义 | `yield str` 就是协议正确 |
| cancellation | finally 清理且取消向上传播 | 用 `except Exception` 假装处理取消 |
| session registry | 只管理本服务创建的任务 | 枚举所有 `asyncio.all_tasks()` |

## Session 1（2h）：可控协程与 timeout

1. 在 `tests/test_async_basics.py` 写三个 fake coroutine：立即返回、等待 Event、抛 DomainError。
2. 预测 `await`、`create_task`、`asyncio.timeout()` 的输出与顺序；用 `pytest.mark.asyncio` 跑测试。
3. 规定 timeout 映射：依赖 timeout → 明确 domain exception → API 503；不把它误判为输入 422。
4. 写一条取消测试：取消 task 后 `finally` 仍记录 cleanup，`CancelledError` 继续向上抛。

## Session 2（2h）：先写流协议，不先写 endpoint

定义本周唯一 SSE contract：

```text
content-type: text/event-stream
event: token     data: {"request_id":"…","text":"…"}
event: error     data: {"request_id":"…","code":"…"}
event: done      data: {"request_id":"…","reason":"finished|cancelled|error"}
每一事件以 \n\n 结束；每请求恰好一个 terminal event。
```

实现 `encode_sse(event, payload) -> str` 纯函数；先测换行、JSON、未知 event、终止事件。注意：纯函数测试不能证明客户端断连行为，后者后续用集成测试验证。

## Session 3（2h）：async generator 和 endpoint

1. 实现 fake async token source（固定 `a,b,c`，无需模型）。
2. `async for` 包装为 SSE；成功流断言 token 顺序和唯一 `done` terminal；source 抛异常则发唯一 `error` terminal，**绝不再发 done**。两类 terminal 总数每请求恰为一。
3. 用支持 stream 的测试客户端测试 headers、frames、终止次数。保存原始 text 到 `experiments/week_05/sample_stream.txt`。

## Session 4（2h）：取消、登记与清理

1. 建小型 `RequestTaskRegistry`：`register(id, task)`, `cancel(id)`, `remove(id)`；只允许本服务创建的 task。
2. 写四个测试：cancel 已登记、未知 ID、重复 cancel、成功结束后 registry 无残留。
3. 取消必须 re-raise `CancelledError`；`finally` 移除 registry 并写 terminal trace。
4. 失败分支：测试挂住 → 用 Event/barrier 控制，不以真实 `sleep` 猜 timing；清理不发生 → 检查 generator close/finally。

## Session 5（2h）：协议报告和资产更新

1. 写 `docs/week_05_stream_contract.md`：事件 schema、状态转移 `created → streaming → finished|cancelled|failed`、明确非目标（reconnect、HTTP/2、多进程）。
2. 在 `workloads/agent_cache_lab/trace_v0.jsonl` 添加脱敏事件，断言每个 `request_id_hash` 只有一个 terminal event。
3. 跑 async/API 全量测试，记录依赖版本与测试插件。完成 [SGLang Lab](SGLANG_LAB.md)。

## 深化

使用无真实网络的 client-disconnect fake；或增加 backpressure 提示字段，但不把复杂队列带进本周。完成需 SSE 纯函数 + stream 集成 + cancel cleanup + trace invariant。
