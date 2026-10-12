# Week 11 板块学习地图

| 板块 | 为什么现在学 | 进入条件 | 定向资料与阅读问题 | 动手目标 | 对应练习/通过证据 | 卡住时回补 |
| --- | --- | --- | --- | --- | --- |
| B1 config/shape | 固定模型才可比较 | W10 tests | Module/LayerNorm：边界？ | validated config | P1；invalid cases | 默认 config 不漂移 |
| B2 block forward | 先证明单步确定性 | B1 | eval/state dict | finite/deterministic logits | P2；B2 isolation | dropout=0/eval |
| B3 generate | token loop 也有边界 | B2 | generate contract | greedy/EOS | P3；edge matrix | stub logits 不训练 |
| B4 local measurement | 指标名称不能误导 | B3 | profiler/reproducibility | prefill/decode raw | P4；术语匹配 | 不叫 TTFT |
| B5 cache seam | Week12 要有基线 | B1–B4 | source policy mapping | replay/golden baseline | P5；no-cache log | 不提前实现 cache |
