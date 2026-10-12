# Week 12 SGLang Lab：radix cache 不变量与上游级测试提案

**时间：核心 2–3h。**

1. 在固定 SHA 定位 `radix_cache.py` 与 session cache 文档；只追踪 `match/insert/evict` 中一条实际符号路径。
2. 写 `sglang_notes/w12_cache_gap_analysis.md`：本地 per-request K/V cache 的不变量；SGLang prefix/session radix cache 额外的所有权、共享、eviction、并发问题；哪些是源码事实（文件/符号/SHA），哪些是待验证推测。
3. 找相关 CPU/unit test，collect/run 并记录结果；失败保留完整环境，不改 main。
4. 写 `sglang_notes/w12_upstream_test_proposal.md`：一个狭窄不变量、最小 synthetic 输入、预期、为何可能回归、需要的 fixture。它是提案，绝不宣称已经修复 issue。

**验收**：固定 SHA、事实/推测分栏、测试证据、一个可审查的测试提案。12 周后的真实下一步是 Week 13+ 双后端 benchmark 或这个提案的最小复现，而不是声称读懂整个项目。
