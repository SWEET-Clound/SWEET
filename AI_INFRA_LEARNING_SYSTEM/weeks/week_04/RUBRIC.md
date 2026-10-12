# Week 4 验收

硬门槛：

- [ ] API request/response schema 有测试；
- [ ] `/health/live` 与 `/health/ready` 行为不同且有测试；
- [ ] 每条 response 有 request ID；
- [ ] 输入/配置/依赖/内部错误可区分；
- [ ] API 测试无网络、无真实模型；
- [ ] 完成第一次月度复盘。

评分：contract 25，ID/日志 20，health 20，fake service 错误测试 20，演示/复盘 15。80/100 通过。

口试：TestClient 为什么不是 E2E？request ID 与 trace ID 有何区别？health 为什么不能调用真实模型？
