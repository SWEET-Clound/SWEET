# Week 9 板块学习地图

| 板块 | 为什么现在学 | 进入条件 | 定向资料与阅读问题 | 动手目标 | 对应练习/通过证据 | 卡住时回补 |
| --- | --- | --- | --- | --- | --- |
| B1 environment | 先确认可运行能力 | Gate0A/独立实验env | tensor attrs：dtype/device？ | capability JSON | P1；CPU/CUDA分支 | CPU first |
| B2 shape/softmax | attention 依赖 shape/数值语义 | B1 torch import | tensor/numerical docs | fixed tensor properties | P2；shape tests | 手算小矩阵 |
| B3 dtype/device | 精度和位置不可混淆 | B2 | numerical accuracy | error/memory ledger | P3；FP32 oracle | 低精度 capability skip |
| B4 timing/profile | 测量有边界 | B3 | profiler docs | raw repeat CSV | P4；30 raw rows | 先 CPU perf_counter |
| B5 report | 观察不是结论 | B1–B4 | 实验模板 | result/limit report | P5；复跑一变量 | 不谈生产 kernel |
