# Week 12 板块学习地图

| 板块 | 为什么现在学 | 进入条件 | 定向资料与阅读问题 | 动手目标 | 对应练习/通过证据 | 卡住时回补 |
| --- | --- | --- | --- | --- | --- |
| B1 cache contract | 先定不变量再写优化 | W11 replay green | SDPA：non-square mask？ | shape/owner rules | P1；negative cases | B=1,T=3 start |
| B2 equivalence | cache 首先是正确性问题 | B1 | local cache note | prefill/decode state | P2；step logits close | CPU FP32 only |
| B3 boundary matrix | 泄漏常在跨请求/边界 | B2 | test matrix | B2/EOS/context | P3；all cases | 一项一 test |
| B4 benchmark | 收益与代价要同测 | B3 | benchmark/report rules | pre-registered CSV | P4；OOM rows | 缩小矩阵并记录 |
| B5 mapping/Gate | 不把 local cache 当 runtime | B1–B4 | vLLM/SGLang docs | gap table + Gate0B | P5；independent rerun | 标 NOT_PROVEN |
