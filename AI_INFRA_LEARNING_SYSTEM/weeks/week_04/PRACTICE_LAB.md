# Week 4 练习实验室

## P1：先写 API contract（B1）

- 起始：`Agents/api_schemas.py`、`tests/test_api_contract.py`。
- 输入：正常 input、空白 input、fake 缺配置、fake timeout。
- 预期：200/422/503/500；所有 JSON 有 request_id；成功 `error=null`；失败无 answer/secret/traceback。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_api_contract.py`。
- 反例：HTTP 200 加 `{error: ...}`；测试必须拒绝。
- 自检：为每个状态写调用者下一步。

## P2：fake service 注入（B2）

- 起始：`Agents/api.py` 的 `create_app(service)`，四个 fake service。
- 输入：success、domain invalid、dependency unavailable、unexpected exception。
- 预期：endpoint 不直接构建真实模型；每种 fake 对应 P1 contract。
- 命令：同 P1；测试不访问网络。
- 反例：endpoint 内 `RealService()`；用 fake 断言其不会被调用。
- 提示：Protocol 写行为，不绑定实现类。

## P3：request ID 全链路（B3）

- 输入：无 `X-Request-ID`、合法 UUID、非法 header、连续两个请求。
- 预期：缺失/非法时新 ID；合法复用；response 和 JSON log ID 一致；两请求不串。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_request_id.py`。
- 反例：每层都 `uuid4()`；断言应失败。
- 产物：`sample_logs.jsonl` 成功/输入错/依赖错各一行。

## P4：live 与 ready 分离（B4）

- 输入：app 正常、必需配置缺失、fake provider unavailable。
- 预期：live 不调用 provider；ready 仅按本周配置/初始化 contract 返回 200/503。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_health.py`。
- 反例：health 调一次真实模型；用 fake call count=0 证明禁止。

## P5：trace JSONL 合同（B5）

- 输入：三个脱敏 response 事件。
- 预期字段：`request_id_hash,started_at_bucket,input_token_estimate,terminal_kind,duration_ms`；不含 `input`、Key、authorization。
- 验证：写测试读取每行 JSON，字段齐全、敏感字段不存在、duration 非负。
- 反例：保存完整 prompt；测试必须失败。
