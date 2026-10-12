# Week 5：异步、流式与取消

> 10 小时核心 + 5 小时深化。主问题：**客户端已经离开时，系统能否停止无意义工作并释放资源？**

从 [START_HERE.md](START_HERE.md) 开始；资料边界在 [RESOURCES.md](RESOURCES.md)，逐题做 [EXERCISES.md](EXERCISES.md)，每次将预测和 task 证据写进 [WORK_LOG.md](WORK_LOG.md)，最后按 [RUBRIC.md](RUBRIC.md) 验收。

## 本周模型

```text
client → stream request → producer task → model/tool await
             │                 │
             └─ disconnect ────┴→ cancellation → finally cleanup → evidence
```

不把“async 函数”误解为“自动更快”。它首先是协调等待、超时、取消和资源生命周期的工具。

## 五个学习板块

### W5-1：协程、Task、Queue 的最小实验（2.5 小时）

**知识：** coroutine 是可暂停计算；Task 将它交给 event loop；`await` 是潜在取消点；Queue 是有界缓冲，容量表达背压策略。

**路径：**

1. 写三个 20 行以内实验：顺序 await、两个 create_task 并发、`Queue(maxsize=2)` 生产/消费；
2. 每个实验先预测总时长和输出顺序；
3. 用 `time.perf_counter` 记录，不用单次结果声称性能结论；
4. 写测试断言最终结果和 queue 不超过上限；
5. 把“并发”与“并行”用自己的语言区别。

**资源：** [asyncio Task 与取消](https://docs.python.org/3/library/asyncio-task.html)，只读 `create_task`、`Task.cancel`、timeout。

**产出：** `experiments/week_05/async_basics.py` 与预测/实际表。

### W5-2：timeout 与取消契约（3 小时）

**知识：** `asyncio.timeout()` 会通过取消当前 task 产生 `TimeoutError`；取消请求不等于任务已结束，清理必须在 `finally`。

**路径：**

1. 写 `slow_tool(delay)`，在 finally 中记录 `cleaned_up=True`；
2. 写 timeout test：短 deadline 必须得到稳定错误且 cleanup 已发生；
3. 写 cancel test：创建 task、取消、await 它、确认 `CancelledError` 被重新抛出；
4. 加一条反例：捕获 `CancelledError` 后吞掉它，观察为何这是 bug；删掉反例；
5. 记录 timeout 与用户主动取消的不同公开文案。

**练习：** 解释为何不能只看 `task.cancel()` 的返回值就断言资源已释放。

**产出 / 验收：** 4+ 取消/超时测试；每条资源路径有 finally 证据。

### W5-3：最小 SSE 协议（3 小时）

**知识：** SSE 是服务端向客户端持续发送事件的 HTTP 响应；事件边界、最终完成事件、错误事件和断开处理都属于契约。它不是 token 速度优化。

**路径：**

1. 先不接模型：写确定的 async generator，产生 `start`、两条 `delta`、`done`；
2. 定义事件 schema：event、request_id、sequence、data；
3. 为 generator 写测试，断言顺序、sequence 单调、最终只有一个 done；
4. 再在 FastAPI 上添加 `/v1/agent/stream`；
5. 使用测试 client 读取完整流，不连真实模型。

**练习：** 设计“第 2 个 delta 发生错误”时，客户端已收到 start 后应看到什么事件；写测试。

**产出：** 事件契约表、6+ 流式单元/组件测试。

### W5-4：断开、背压前哨与不遗留任务（3 小时）

**知识：** 取消必须沿调用链传播。CPU 密集函数没有 await 时不会立刻响应取消；本周只处理 async I/O 模拟，CPU 任务留到性能阶段。

**路径：**

1. 为 streaming producer 保留 task 引用；
2. 模拟客户端断开/消费者提前停止；
3. 在 `finally` 关闭 queue、取消 child task、等待其结束；
4. 测试前后调用 `asyncio.all_tasks()` 或受控 task registry，证明没有遗留本周创建的 task；
5. 对 queue 满时明确策略：等待、丢弃、还是拒绝；本周选择一种并测试。

**练习：** 对慢消费者画时间线：producer、queue、consumer、cancel 四条线。

**产出 / 验收：** 断开后 cleanup 测试；一份 task 生命周期 trace。

### W5-5：运行证据与回归（3.5 小时）

1. 跑所有同步/异步测试 5 次，记录波动与失败；
2. 人工触发 timeout、主动取消、正常流三条路径；
3. 更新 failure catalog：timeout、cancel suppressed、queue full、disconnect、double done；
4. 写 `experiments/week_05/cancellation_report.md`：契约、证据、未覆盖的真实 provider 取消能力；
5. 口试：取消、timeout、disconnect、backpressure 的差别。

**本周 DoD：** 超时和取消均有测试；断开不遗留受控后台任务；流可重复读取；不宣称已能停止任意第三方模型调用。总测试建议 40+。
