# Week 6 板块学习地图

| 板块 | 为什么现在学 | 进入条件 | 定向资料与阅读问题 | 动手目标 | 对应练习/通过证据 | 卡住时回补 |
| --- | --- | --- | --- | --- | --- |
| B1 admission | 无限创建 task 会掩盖过载 | Week5 cleanup | asyncio sync：Semaphore 限什么？ | max in-flight | P1；peak≤N | Event/barrier 控制 |
| B2 bounded queue | 服务要明确拒绝或等待 | B1 | Queue：full 时怎样？ | queue+status contract | P2；queue-full test | 固定只选 429/503 |
| B3 deterministic workload | 真实比较要可回放输入 | B2 | SGLang benchmark：rate/concurrency区别？ | JSONL generator | P3；same seed same bytes | 先生成 3 行 |
| B4 statistics | 一次平均值不能表示尾部 | B3 | statistics：P95 定义？ | quantile function | P4；边界测试 | 明确 nearest-rank |
| B5 load report | 用原始数据解释策略 | B1–B4 | raw output schema | fixed experiment matrix | P5；raw+report | fake 服务不谈真实吞吐 |
