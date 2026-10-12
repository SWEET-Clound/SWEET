# Week 4：HTTP 服务、请求身份与月度复盘

> 10 小时核心 + 5 小时深化。主问题：**Agent 能否被一个外部调用者安全调用，并在问题发生时说明是哪一次请求、哪一类错误？**

使用顺序：[START_HERE.md](START_HERE.md) → 当前 README 的五个板块 → [EXERCISES.md](EXERCISES.md) → [WORK_LOG.md](WORK_LOG.md) → [RUBRIC.md](RUBRIC.md)。资料只读 [RESOURCES.md](RESOURCES.md) 指定部分。

## 本周边界

将 Week 3 的 service 接到一个最小 FastAPI API。只做单进程本地服务，不部署云、不加数据库、不让真实模型成为测试前提。

```text
HTTP request → validate schema → assign request_id → service → response/error
                                      ↓
                                 structured log
```

## 五个学习板块

### W4-1：HTTP 与 API 契约（2.5 小时）

**知识：** HTTP 方法表达意图；状态码表达结果类别；请求/响应 schema 是双方契约。`400/422` 是用户请求不满足 contract，`503` 是依赖暂不可用，`500` 是未预期 bug。

**路径：**

1. 定义 `POST /v1/agent/responses` 的 request：`input`、可选 `thread_id`；
2. 定义 response：`request_id`、`answer`、`task_type`、`error`（成功时为 null）；
3. 写 4 张例子卡：正常、空输入、缺配置、未知内部异常；
4. 先用 Pydantic schema 写 API 测试，再写 endpoint；
5. 为每个状态码写“调用者下一步应做什么”。

**练习：** 判断 401、403、422、429、503、500 中哪些本周使用、哪些暂不使用，并写理由。

**产出 / 验收：** `api.py`、schema 表、4 个 contract tests。不能把原始 provider error 直接塞进 JSON。

### W4-2：request ID 与结构化日志（3 小时）

**知识：** request ID 是一次请求的关联键；结构化日志是可查询字段而非漂亮的句子。日志应有时间、级别、request_id、事件名、耗时、错误类别，不应有 Key 或完整敏感 prompt。

**路径：**

1. 读取或生成 `X-Request-ID`；格式不合法时生成新 ID；
2. 在 request 开始、路由完成、结束/异常各记一条 JSON log；
3. 用 dependency 或 middleware 保证同一 request ID 流过 service；
4. 写测试断言 response 和 log 使用同一个 ID；
5. 用两个并发 test client 请求证明日志没有串 ID。

**练习：** 给字段分类：必须记录、可选记录、绝不能记录（API Key、完整 authorization、原始用户隐私内容）。

**产出 / 验收：** 一份 `experiments/week_04/sample_logs.jsonl`，含成功/输入错误/依赖错误各一例。

### W4-3：健康检查不是模型调用（2.5 小时）

**知识：** liveness 问“进程活着吗”；readiness 问“是否能接流量”。本地最小版 health 不应因为真实 LLM 网络暂时失败而挂死。

**路径：**

1. `GET /health/live` 只报告进程；
2. `GET /health/ready` 检查必要配置/本地依赖初始化状态，不实际调用模型；
3. 定义何时返回 200 与 503；
4. 用 TestClient 为三种状态写测试：正常、缺 Key、服务构造异常；
5. 写 README 说明健康检查覆盖什么、不覆盖什么。

**资源：** [FastAPI TestClient](https://fastapi.tiangolo.com/tutorial/testing/)；它允许直接测试 app，不必真实开启 socket。

**产出 / 验收：** 至少 6 个 API/health 集成测试；测试不得访问公网。

### W4-4：错误边界与 fake service（3 小时）

**知识：** endpoint 测 HTTP contract，service 测业务，provider adapter 测外部边界。fake service 是一个符合接口、但返回确定结果的实现；它不是伪造真实 LLM 质量。

**路径：**

1. 定义 `AgentService` Protocol：输入、request_id、返回 response 或明确异常；
2. 测试中注入 fake：成功、超时、配置错、未知错；
3. 把异常映射到 422 / 503 / 500；
4. 断言每种 JSON response 都含 request_id，但不含 stack trace；
5. 手动 curl 一次本地服务，核对与 TestClient 行为一致。

**练习：** 写一个“为何不在 API 测试中 mock `print` 或 patch 全局 os.environ”的解释，并指出应注入的位置。

**产出 / 验收：** 端到端边界图，至少 10 个 HTTP 层测试。

### W4-5：第一次月度复盘（4 小时）

**路径：**

1. 运行质量门、全部测试和 API 测试；
2. 从 Week 1 至今汇总：测试数量、环境重建时间、已知失败类、未覆盖风险；
3. 对比“初始单文件 demo”与“有 API/ID/health 的服务”，明确仍缺什么；
4. 在 `reviews/YYYY-W04.md` 用模板写至少 1,000 字证据复盘；
5. 做 10 分钟演示：调用正常/空输入/缺配置三条路径。

**本周 DoD：** API 与 health 有集成测试；每个 response 有 request ID；错误分类正确；月度复盘完成；总测试建议 30+。80/100 才进入异步。
