# Week 4 板块学习地图

| 板块 | 为什么现在学 | 进入条件 | 定向资料与阅读问题 | 动手目标 | 对应练习/通过证据 | 卡住时回补 |
| --- | --- | --- | --- | --- | --- |
| B1 HTTP contract | 外部调用者需要稳定语言 | Week3 quality gate | FastAPI status：422/503/500 谁负责？ | 请求/响应 schema | P1；四个 JSON contract | 先写例子，不写 endpoint |
| B2 App factory | API 测试不应启动真实模型 | B1 tests | FastAPI testing：TestClient 测什么？ | `create_app(fake)` | P2；四种 fake | 将实例化移到 composition root |
| B3 request identity | 失败必须定位到一次请求 | B2 endpoint | logging：为何字段化且脱敏？ | header→service→log | P3；ID 一致 | 固定 ID factory 再测 |
| B4 health/error | 进程活着不等于依赖可用 | B3 logs | IANA：本周为何不用 401/429？ | live/ready 和异常映射 | P4；独立 health tests | 先做 fake unavailable |
| B5 trace asset | 将服务行为变成未来 workload | B1–B4 绿 | 复读 schema/隐私规则 | 脱敏 trace v0 | P5；JSONL invariant | 仅记录长度/类别/hash |
