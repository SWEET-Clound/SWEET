# Week 7 SGLang Lab：请求生命周期与 scheduler trace

**时间：核心 2h。**

1. 在固定 SHA 中定位 `scheduler.py`（按实际路径）并只选择**一条**请求进入/完成/失败的调用线。
2. 用静态方式建立不超过 8 个节点的图：输入对象、队列/状态对象、完成/异常出口。每一箭头附源码文件与符号。
3. 用 `rg -n "trace|log|request_id|abort|finish" <scheduler path>` 找一个现存 log/test 观察点；执行相关 test collect-only，若能跑则保存结果。
4. 写 `sglang_notes/w07_scheduler_trace.md`：本地 trace 的 span 如何映射，哪些生命周期不能从静态阅读断言；列一个要由 benchmark/测试验证的问题。

**验收**：SHA、调用图、实际符号、collect/run、一个“未证明”问题。不得将读源码等同于理解调度算法。
