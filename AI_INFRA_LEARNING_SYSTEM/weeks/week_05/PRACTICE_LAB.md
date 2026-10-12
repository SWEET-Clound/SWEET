# Week 5 练习实验室

## P1：timeout 与取消传播（B1）

- 起始：`tests/test_async_basics.py`，fake coroutine 由 `Event` 控制。
- 输入：立即返回、永不释放 Event、被 cancel 的 task、domain exception。
- 预期：timeout 转成已定义错误；cancel 时 finally 运行且 `CancelledError` 继续传播。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_async_basics.py`。
- 反例：`except Exception` 吞 cancellation；断言 task 不应假装 success。

## P2：SSE 编码器（B2）

- 起始：`encode_sse(event, payload)` 与 `tests/test_sse_encoding.py`。
- 输入：token、error、done、未知 event、payload 含换行/Unicode。
- 预期：`event:` + JSON `data:` + 双换行；未知 event 明确拒绝；payload 可被 JSON parse。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_sse_encoding.py`。
- 反例：没有 `\n\n` 或 `str(dict)`；测试必须失败。

## P3：成功/失败流（B3）

- 输入：async source 依次 `a,b,c`；另一 source 在 b 后抛 dependency error。
- 预期：成功 token 顺序且一个 done；失败有合同定义的 error/terminal，不多于一个 terminal。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_streaming.py`。
- 产物：`sample_stream.txt` 保存原始 frames。

## P4：registry 清理（B4）

- 输入：已登记 ID、未知 ID、重复 cancel、success completion。
- 预期：已登记 task 可取消；未知不误伤；结束/取消后 registry 空；不使用 `asyncio.all_tasks()`。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_request_registry.py`。
- 反例：finally 未 remove；teardown 应捕捉残留。

## P5：terminal event 不变量（B5）

- 输入：一条 success、cancelled、failed trace，以及故意两条 done 的坏 trace。
- 预期：每 `request_id_hash` 恰一条 terminal；坏 trace 被 validator 拒绝。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_trace_invariants.py`。
- 自检：说明 task cleanup 与 SGLang session cache close 为什么不是同一件事。
