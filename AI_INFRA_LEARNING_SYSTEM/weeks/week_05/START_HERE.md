# Week 5 从这里开始

本周只学习等待、超时、取消、流式事件和资源清理。`async` 不等于更快；它的第一价值是让你能控制“何时停止”。

进入 Gate：Week 4 API/health 测试通过。先在 20 行实验中理解 Task 和 finally，后接入 Agent；不要直接把 LangGraph streaming 当作学习入口。

每次遇到取消，都必须回答：谁请求取消？谁接收到 `CancelledError`？哪个 finally 负责清理？测试如何证明没有遗留 task？
