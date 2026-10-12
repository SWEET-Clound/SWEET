# Week 10 板块学习地图

| 板块 | 为什么现在学 | 进入条件 | 定向资料与阅读问题 | 动手目标 | 对应练习/通过证据 | 卡住时回补 |
| --- | --- | --- | --- | --- | --- |
| B1 shape/maths | 先知道 attention 算什么 | W9 shape tests | SDPA：shape/mask？ | fixed handcalc | P1；golden output | B=H=1,T=2,D=1 |
| B2 causal property | future 必须不可见 | B1 | paper：mask保障什么？ | reference mask | P2；future mutation | 先测试 past index |
| B3 multihead/oracle | 结构/实现要分开证 | B2 | MHA/SDPA | split/merge + oracle | P3；H=1/ValueError | CPU FP32 only |
| B4 cost | 理论规模要与数据对照 | B3 | profiler/timing回看 | complexity CSV | P4；T matrix | 不推生产性能 |
| B5 failure report | 失败要可定位 | B1–B4 | numerical accuracy | failure catalog | P5；mutation red | 一个错误一次 |
