# Week 5 SGLang Lab：session-aware cache 的生命周期

**时间：核心 2h。** 使用 Week 3 记录的 SHA，不升级代码。

1. 读该版本的 session/radix-cache 文档，只回答：session ID 如何影响复用？`close_session` 的语义是什么？为何客户端断开不必然等于 cache 可删除？
2. 在 clone 用 `rg -n "close_session|session_id|radix" python tests docs` 找至多五个候选符号/测试；记录真实路径。
3. 写 `sglang_notes/w05_session_lifecycle.md`：状态图 `new → active → terminal → close/evict`；每条边标注触发者和资源所有者。
4. 选择一条相关测试，先 `--collect-only`；若可以运行，只记录 pass/fail，不改变上游。
5. 与本地 registry 对照：本地 task 清理 ≠ SGLang prefix/session cache 回收；写至少两个不能类比处。

**验收**：固定 SHA、状态图、符号与测试证据、两个不可类比点。禁止从 docs 推断真实 eviction 实现。
