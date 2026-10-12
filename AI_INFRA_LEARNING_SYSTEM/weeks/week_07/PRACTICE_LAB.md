# Week 7 练习实验室

## P1：确定性 trace tree（B1）

- 起始：`TraceRecorder`、`tests/test_trace_tree.py`；注入 fixed clock 和 IDs。
- 输入：`http.request → service.run → dependency.call` 三个 span。
- 预期：parent 关系正确、`ended_ns >= started_ns`、span 不含 prompt/key/authorization。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_trace_tree.py`。
- 反例：dependency 不 close 或 parent_id 错；validator 必须失败。
- 提示：先把 span 当 JSON dict，不必先接 exporter。

## P2：错误三向一致性（B2）

- 输入：invalid input、timeout、transient 503、permanent 4xx、queue full 五种 fake。
- 预期：每类都有既定 HTTP/terminal error_code；response、log、span 的 error_code 一致；秘密字段不存在。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_fault_mapping.py`。
- 反例：response 是 503、span 写 internal；测试应红。

## P3：受限 retry（B3）

- 起始：`retry_call(fn, clock, max_attempts=2, deadline_ms=...)`。
- 输入：一次 timeout 后 success、永久 4xx、持续 timeout、side-effect fake。
- 预期：transient 至多2次；permanent/side-effect call_count=1；deadline 到即停；每 attempt 入 trace。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_retry.py`。
- 反例：把 retry 包整个 request 或吞 timeout；attempt/call_count 断言捕捉。

## P4：90 秒 trace 定位（B4）

- 输入：保存的 `fixtures/trace_timeout.jsonl`，包含固定 trace_id/span_id/attempt。
- 任务：回答失败层、是否重试、停止原因、用户看到什么、哪个字段支持结论。
- 验证：`tests/test_trace_answers.py` 检查你填写的 JSON answer 与 fixture facts 一致。
- 反例：凭 HTTP status 猜 dependency 层；必须引用 span。

## P5：可复现 RCA（B5）

- 输入：P2 任选一个 fake fault。
- 预期产物：`experiments/week_07/fault_<name>.md`，含假设、fixture、命令、raw trace、结果、限制、对应 label。
- 自检：另一个人只看该文件能重跑；没有 raw trace 或 command 为 0 分。
