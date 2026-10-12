# Week 3–12 SGLang 源码练习册

先完成 [源码工作流](SGLANG_SOURCE_WORKFLOW.md) 的 version lock。每题先在 `SGLANG_TEST_MATRIX.md` 登记 L0/L1/L2，再写产物；所有事实使用 `SHA:path:symbol`。每题评分按工作流六项 0–2，总分 ≥9/12。

| ID | 前置 | 精确任务 | 最低测试/命令 | 产物 / 未证明项 |
| --- | --- | --- | --- | --- |
| SGL-03-A | clone | 选择 tag、detached checkout、写 source lock | `git status --short` + `rev-parse HEAD` | `source_lock.json` / 不证明功能 |
| SGL-03-B | lock | repo 与 test map，至多 6 目录/符号 | `rg -n "pytest|test_" pyproject.toml docs tests` | `w03_repo_map.md` / 不证明运行路径 |
| SGL-03-C | map | 找 CPU-safe test | `pytest --collect-only -q <discovered path>` | matrix row / collect 非 pass |
| SGL-04-A | lock | 从 doc+server+test 提取六字段 API contract | collect relevant test | `w04_api_contract.md` / 不证明 server E2E |
| SGL-04-B | 04-A | 本地与 SGLang 字段差异、3 JSON例 | N/A | compare table / 不推鉴权/stream |
| SGL-05-A | lock | session `new→active→terminal→close/evict` | collect cache/session test | lifecycle graph / 不推 eviction |
| SGL-05-B | 05-A | request task/session/prefix 所有权 truth table | N/A | 8-operation table / 不推并发 |
| SGL-06-A | lock | benchmark JSONL/input-output字段映射 | collect benchmark/unit test | data schema card / 不推真实性能 |
| SGL-06-B | 06-A | fake queue 与 TTFT/ITL/TPOT 8项可比判断 | N/A | metric matrix / fake≠serving |
| SGL-07-A | lock | request entry→finish/fail ≤8节点 | collect scheduler related test | call graph / 静态不等运行 |
| SGL-07-B | 07-A | request state table + log/trace观察点 | `rg -n "abort|finish|trace|log" <path>` | state table / 未证状态转换 |
| SGL-08-A | lock | 分栏 issue主张/源码事实/本地假设 | collect discovered test | `NOT_REPRODUCED` card / 不伪称复现 |
| SGL-08-B | 08-A | 最小复现设计与 fixture | N/A | test draft / 不提交上游 |
| SGL-09-A | lock | 三符号 shape/dtype/device ledger | collect CPU test | inventory / unknown 保留 |
| SGL-09-B | 09-A | 本地 FP32 验证一个可类比 shape rule | local pytest | mapping / 不推 runtime memory |
| SGL-10-A | lock | backend interface 的 Q/K/V/mask/cache 证据 | collect related test | field table / 不推 kernel |
| SGL-10-B | 10-A | local causal reference 证明/不证明矩阵 | local pytest | boundary matrix / 不推 backend performance |
| SGL-11-A | lock | waiting/prefill/decode 实际命名与 policy branch | collect policy test | decision graph / 不推 scheduling result |
| SGL-11-B | 11-A | 两个 synthetic requests 的 branch解释 | N/A | branch note / local timing≠TTFT |
| SGL-12-A | lock | 追踪 match/insert/evict 之一 | collect cache test | facts-hypotheses table / 不推 all cache |
| SGL-12-B | 12-A | 上游测试提案：input/invariant/fixture/risk | N/A | proposal / `NOT_REPRODUCED` allowed |

## 固定反例题

每周任选一个：将网页 `main` 的事实写成 SHA 事实、将 collect-only 写成 pass、将静态调用图写成运行轨迹、将 local cache 写成 radix cache。你的审计应标红并写出正确表述。它们都不能获得本周 SGLang 练习分。
