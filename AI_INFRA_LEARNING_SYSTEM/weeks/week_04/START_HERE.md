# Week 4 从这里开始

本周把 Week 3 的可测试 service 暴露为一个最小 HTTP API。目标不是上线，而是让调用者能得到稳定 schema、request ID 与可测试错误。

进入 Gate：Week 3 质量门通过；不使用真实模型做 API 测试；先运行全部离线测试。

每次学习顺序：先画 request/response contract → 写 TestClient 测试 → 实现 endpoint → 记录 response/log → 做口头解释。

结束时能证明：同一 request ID 出现在 response 与日志；空输入/配置缺失/依赖故障不是同一个 HTTP 结果。
