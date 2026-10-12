# Week 7：Tracing、故障注入与恢复语义

> 10 小时核心 + 5 小时深化。主问题：**一次失败能否从请求到具体组件定位，并证明重试不会重复副作用？**

按 [START_HERE.md](START_HERE.md) 做 trace 设计；资料边界在 [RESOURCES.md](RESOURCES.md)，先用 [EXERCISES.md](EXERCISES.md) 建故障矩阵，所有 request ID、span 与结论记录到 [WORK_LOG.md](WORK_LOG.md)，最后按 [RUBRIC.md](RUBRIC.md) 审计。

## 本周的可观测性契约

```text
request span
  ├─ route span
  ├─ queue/admission span
  ├─ tool/model span
  └─ response span
      attributes: request_id, task_type, outcome, duration_ms
```

trace 用来回答“发生了什么、在哪里、耗时多久”；日志保留事件细节；metrics 用来发现群体趋势。三者不能相互替代。

## 五个学习板块

### W7-1：先设计 span，再安装工具（2.5 小时）

**知识：** span 是有开始/结束的操作；trace 由有父子关系的 span 组成；attribute 是低基数字段；event 是 span 内事件。用户原文、Key、完整 prompt 不能成为 attribute。

**路径：**

1. 对正常请求画 trace 树；
2. 为每个 span 写：开始条件、结束条件、必须字段、禁止字段、错误状态；
3. 规定 outcome 枚举：success/input_error/config_error/dependency_error/timeout/cancelled/internal_error；
4. 先写 in-memory span recorder fake 和测试，再考虑 OTel exporter；
5. 断言异常路径的 parent/child 关系仍正确闭合。

**资源：** [OpenTelemetry Python 手动埋点](https://opentelemetry.io/docs/languages/python/instrumentation/)，只读 tracer、nested spans、attributes、record exception。

**产出：** `trace_contract.md` 与 5 个 span contract tests。

### W7-2：最小实现与时间一致性（3 小时）

**路径：**

1. 在 request、route、admission、tool/model 四个边界建立 span；
2. 使用 monotonic clock 测 duration，不用墙钟相减；
3. 将 request_id 作为 log/trace correlation 字段，不把它当 trace_id 伪造；
4. 测试 success、input error、timeout 三条路径都结束 span；
5. 导出一份 JSON trace，人工核对 child duration 不应无故超过 parent duration。

**练习：** 人为制造 route 后抛错，预测哪些 span status 为 ERROR、哪个 span 仍是根。

**产出 / 验收：** 三条 trace JSON 与一致性测试。

### W7-3：故障注入矩阵（3.5 小时）

**知识：** 注入是主动制造确定失败，验证检测、隔离、用户响应和恢复。它不是随机把网络拔掉。

| 故障 | 注入点 | 预期公开结果 | trace 证据 |
| --- | --- | --- | --- |
| timeout | fake model sleep | dependency/timeout | model span timeout |
| 429 | provider adapter fake | dependency error | status + retry decision |
| 500 | provider adapter fake | dependency error | exception event |
| 非法 tool 输入 | core tool | input error | tool span outcome |
| queue full | admission | overload | queue span reject |

**路径：** 为每行先写测试，再接入 fake。每种失败必须能从 request_id 找到具体失败 span，且公开响应不泄露内部堆栈。

**产出 / 验收：** 5 类故障、5 份 trace、对应测试与 failure taxonomy 初稿。

### W7-4：重试与幂等边界（3 小时）

**知识：** retry 只能用于明确可重试的临时依赖故障；幂等操作重复执行结果相同。工具若有副作用（发邮件、写文件、下单），重试前必须有 idempotency key 或禁止自动重试。

**路径：**

1. 给每个现有 tool 标“纯 / 可重读 / 有副作用”；
2. 为 fake provider 制定最多一次 retry、指数等待由 fake clock 控制；
3. 写测试：429 后第二次成功；输入错误绝不 retry；超出次数停止；
4. 建一个计数型 fake side-effect tool，证明无 idempotency 时 retry 会重复；
5. 选择修复：不 retry 或按 request_id 去重，并写 ADR。

**练习：** 分析“模型返回超时但服务端其实完成了工具调用”的不确定状态，说明为何不能自动盲重试。

**产出：** retry policy、ADR、4+ 重试测试。

### W7-5：故障报告与定位演练（3 小时）

1. 从五类故障中随机抽 3 类，不看代码只用 trace/log 定位；
2. 找到一个由 tracing 揭示的问题（例如 span 没关闭、错误分类错、队列等待未记录）并修复；
3. 写 `failure_taxonomy.md`：触发、影响、信号、隔离、测试、恢复、owner；
4. 运行回归与质量门；
5. 演示“给定 request_id 的 90 秒定位”。

**本周 DoD：** 五类失败均可从 trace 定位；至少一处由 trace 驱动修复；重试语义有测试；总测试建议 60+。
