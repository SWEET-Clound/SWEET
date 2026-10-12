# Week 4 练习册

## E1：API contract 卡

为 `POST /v1/agent/responses` 写正常、空 input、配置缺失、依赖超时四个 JSON 请求/响应例子。每个例子必须有 status、`request_id`、调用者下一步。

## E2：状态码判断

将空 input、格式错、Key 缺失、模型超时、未知 bug 映射到状态码；解释为何不使用 200 + `"error"`。

## E3：request ID

先写测试：无 header 时生成 ID；合法 header 时复用；非法 header 时新建；response 和 log ID 相同。再实现。

## E4：health 的范围

写 `live` 与 `ready` 的对比表：检查什么、不检查什么、何时 200/503。测试“模型网络不可用但 liveness 仍应成功”。

## E5：fake service

实现/设计四种 fake：成功、输入错、依赖超时、内部异常。测试 endpoint 不泄露 traceback/Key/绝对路径。

## E6：月度演示

按脚本运行三次：成功、空输入、缺配置。每次保存 response、相同 request ID 的日志和对应测试名。
