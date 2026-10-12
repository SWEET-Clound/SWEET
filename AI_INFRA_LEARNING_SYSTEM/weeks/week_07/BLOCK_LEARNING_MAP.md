# Week 7 板块学习地图

| 板块 | 为什么现在学 | 进入条件 | 定向资料与阅读问题 | 动手目标 | 对应练习/通过证据 | 卡住时回补 |
| --- | --- | --- | --- | --- | --- |
| B1 trace schema | 日志无法表达因果关系 | Week6 raw trace | OTel：trace/span/parent 是什么？ | fixed IDs/clock 的 span | P1；tree validator | 从 3 span 成功树开始 |
| B2 fault taxonomy | 失败需稳定地到达调用者 | B1 | exceptions：finally为何重要？ | fake error→HTTP→span | P2；三向一致 | 先只测 timeout |
| B3 retry boundary | 重试可能放大副作用 | B2 | RFC：idempotent 意味什么？ | allowlist+deadline | P3；attempt/call count | 固定 max=2/clock |
| B4 trace diagnosis | 观测必须能定位问题 | B1–B3 | 重新读自己的 trace | 90秒诊断表 | P4；给定trace答题 | 只追一条 request |
| B5 fault asset | 失败可复现才是知识 | B4 | failure label contract | taxonomy+RCA | P5；完整故障证据 | 选一例，不扩类别 |
