# Week 5 验收

- [ ] 有顺序/并发/Queue 三个最小实验；
- [ ] timeout 与主动取消分别有测试；
- [ ] 每条取消路径均有 finally cleanup 证据；
- [ ] SSE 事件 contract 有测试；
- [ ] 断开不遗留本周创建的 task；
- [ ] 不声称能取消任意第三方 provider 调用。

评分：基本模型 20，timeout/cancel 25，SSE 20，断开与 task 审计 25，报告 10。80 分通过。
