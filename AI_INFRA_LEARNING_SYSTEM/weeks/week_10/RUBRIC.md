# Week 10 验收

- [ ] shape flow 不看代码可解释；
- [ ] 单头 causal reference 对未来不可见有反例测试；
- [ ] 多头 split/merge 有 shape 与错误测试；
- [ ] 与 SDPA 在定义容差内一致；
- [ ] 不一致诊断有最小复现；
- [ ] 有 T 变化的复杂度/trace 证据。

评分：shape/手算 20，单头正确性 25，多头 20，oracle 对照 25，解释 10。80 分通过。
