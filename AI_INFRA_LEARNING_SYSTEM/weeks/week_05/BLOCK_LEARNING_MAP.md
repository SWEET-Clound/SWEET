# Week 5 板块学习地图

| 板块 | 为什么现在学 | 进入条件 | 定向资料与阅读问题 | 动手目标 | 对应练习/通过证据 | 卡住时回补 |
| --- | --- | --- | --- | --- | --- |
| B1 coroutine/timeout | 先理解控制流，再接流 | Week4 API tests | asyncio：task、timeout、cancel 区别？ | controlled fake coroutines | P1；4 async tests | Event 取代 sleep |
| B2 SSE frame | 流不是普通字符串拼接 | B1 | MDN SSE：frame 边界/terminal？ | pure encoder | P2；frame tests | 先测试 `\n\n` |
| B3 async stream | 事件顺序与错误必须稳定 | B2 | async generator：finally 何时跑？ | fake token source | P3；success/error stream | 先保存 raw text |
| B4 cancel/ownership | 取消不能泄漏 task | B3 | asyncio cancel：为什么 re-raise？ | small registry | P4；cleanup tests | 只管自己创建的 task |
| B5 trace lifecycle | 为 cache/session 问题积累证据 | B1–B4 | session cache 对照 | terminal event invariant | P5；每请求一个结束 | 先用两条 synthetic event |
