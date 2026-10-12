# Week 7 验收

- [ ] trace contract 不含 Key/完整 prompt；
- [ ] success/input error/timeout 都闭合所有 span；
- [ ] 五类注入故障均有 trace 和测试；
- [ ] retry 有明确上限且输入错不 retry；
- [ ] 有副作用工具的重复执行风险被证明并处理；
- [ ] 至少一个问题由 trace 发现并修复。

评分：contract 15，埋点正确性 20，故障矩阵 30，retry/幂等 20，定位演练 15。80 分通过。
