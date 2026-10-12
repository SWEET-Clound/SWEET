# Week 7 练习册

## E1：trace contract

为 request、route、admission、tool/model、response 填开始/结束条件、必须 attribute、禁止 attribute、错误 status。

## E2：正常和错误树

画成功与“model timeout”两棵树。标 parent/child、request_id、outcome 和 duration。

## E3：五类故障矩阵

对 timeout、429、500、非法 tool 输入、queue full 各写一次 deterministic fake 注入、预期 response、span 证据、测试名。

## E4：retry 分类

分类：输入错误、429、500、timeout、side-effect tool 已执行但 client 超时。写“retry / 不 retry / 人工确认”和原因。

## E5：副作用反例

做计数 fake tool；模拟第一次调用后响应丢失。证明盲重试调用两次，再加 request_id 去重或禁止自动重试。

## E6：90 秒定位

随机选一个保存的 request_id，只用 log/trace 回答：哪里失败、为何分类、重试了吗、用户看到什么、哪个测试防回归。
