# Week 4 逐次执行手册：HTTP 契约、身份与可测服务

> 核心 10h + 深化 5h。目标是一个**单进程、离线可测试**的 API 边界，不是上线服务。

## 进入 Gate

运行 Week 3 质量门；若没有 20+ 绿测、ADR 或质量命令，本周只花 2h 回补。确认测试不调用真实模型、网络或 Key。

## 知识地图与稳定 contract

| 概念 | 当前实现 | 关键不变量 | 反例 |
| --- | --- | --- | --- |
| Schema | `AgentRequest/AgentResponse` | 每个 response 都有 request_id | 返回随意字典 |
| 4xx/5xx | 异常→HTTP 映射 | 用户输入不当与依赖失败不同 | 所有失败都 500 |
| request ID | header/middleware/service | 一个请求全链路同一 ID | 每层重新生成 ID |
| fake service | `create_app(service)` | API 测试零网络 | endpoint 内部实例化真实模型 |
| health | live/ready | liveness 不做模型调用 | health 因上游超时卡住 |

**本周数据契约（先写测试）**：

```text
POST /v1/agent/responses
request: {input: nonblank str, thread_id?: nonblank str}
success 200: {request_id: str, answer: str, task_type: str, error: null}
invalid 422: {request_id: str, error: {code, message}}
dependency 503 / unexpected 500: same error shape; no traceback
```

## Session 1（2h）：先定义 schema 和状态决策表

1. 创建 `Agents/api_schemas.py` 和 `tests/test_api_contract.py`（映射到真实 package）。
2. 为正常、空 input、缺配置、超时、未知异常写 JSON 例子和测试断言：status、字段、无 secret/path/traceback。
3. 写 `docs/week_04_status_decision.md`：本周只用 200/422/503/500；明确 401、429 不实现的理由。
4. 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_api_contract.py`。起初可红；完成后必须绿。

## Session 2（2h）：App factory 与 fake 注入

1. 定义最小 Protocol：`run(input: str, *, request_id: str, thread_id: str | None) -> ServiceResult`。
2. 实现 `create_app(service)`，禁止 endpoint `new RealModel()`；生产入口只在 composition root 注入真实 service。
3. 写四个 fake：success、validation/domain error、dependency unavailable、unexpected error。
4. 用 FastAPI `TestClient` 跑 endpoint；不启动 socket，不 mock 全局 `os.environ`。

**失败分支**：TestClient import 失败→先记录 FastAPI/starlette/httpx 版本并按官方兼容组合修复；不要为测试改成真实 HTTP server。

## Session 3（2h）：request ID 与 JSON 日志

1. 规定合法 header 格式（UUID 或本周定义的有限字符），非法/缺失时生成新 ID；在测试中固定 UUID factory，保证确定性。
2. 定义 JSONL 字段：`timestamp, level, event, request_id, thread_id_present, input_chars, status_code, error_code, duration_ms`。
3. 写开始、完成、失败三事件；日志只存长度/类别，不存 prompt 和任何 authorization。
4. 测试：合法 ID 复用、非法重生、response/log 同 ID、两次请求不串 ID。

## Session 4（2h）：health 与边界错误

1. `GET /health/live` 只说明进程/应用构建成功；`GET /health/ready` 仅检查必需配置和可注入 service 是否就绪。
2. 为 ready 的 200/503 和 live 的独立性写测试；即使 provider 被 fake 为 unavailable，liveness 仍应通过。
3. 手动启动一次本地 app 后用 `Invoke-RestMethod` 调成功与空输入；保存 response/log 摘要。随后停止进程，不能让服务长时间占用终端。

## Session 5（2h）：月度审计与价值资产 v0

1. 跑 Week 3 质量门与 API 测试；总数目标 30+，但以测试边界为准而非凑数。
2. 建 `workloads/agent_cache_lab/schema_v0.md`，定义匿名 trace：`request_id_hash, started_at_bucket, input_token_estimate, thread_id_hash?, terminal_kind, duration_ms`；写禁止字段。
3. 写 `reviews/YYYY-W04.md`：事实、命令、失败、未测的认证/数据库/多进程风险、下周假设。

## 深化与完成定义

深化：为响应 schema 增加 OpenAPI 例子，或用两个 fake 做并发隔离测试。完成需：contract 测试、factory+fake、ID/log JSONL、health、月度复盘及 [SGLang Lab](SGLANG_LAB.md)。
