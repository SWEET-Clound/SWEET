# Week 8 SGLang Lab：issue triage 与最小复现设计

**时间：核心 2h。**

1. 固定 SHA 后，在 issues 中筛选一个与 cache/scheduler/test 有关的公开问题；记录 URL、访问日期、标签、最后活动日期，说明它会变化。
2. 写 `sglang_notes/w08_issue_card.md`：问题主张、已有证据、缺失证据、受影响版本、最小输入、CPU test 是否可能、风险、明确的“本周不做”事项。
3. 在 clone 中找相关测试/符号，做 `pytest --collect-only`，不能运行则记录阻塞。
4. 产出**本地**的设计型测试草稿（不改上游）：给定输入、期望不变量、为何可能捕获问题。不得伪称已经复现。

**验收**：issue card 区分事实/推测，含 SHA 和测试证据；没有复现就写 `NOT_REPRODUCED`。
