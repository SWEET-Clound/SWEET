# Week 3 板块学习地图

按顺序完成。每个 P 题在 [练习实验室](PRACTICE_LAB.md) 中；概念说明在 [README](README.md)，逐步时间表在 [DETAILED_PATH](DETAILED_PATH.md)。

| 板块 | 为什么现在学 | 进入条件 | 定向资料与阅读问题 | 动手目标 | 对应练习/通过证据 | 卡住时回补 |
| --- | --- | --- | --- | --- | --- |
| B1 边界与 ADR | 先冻结行为，才知道重构没有改业务 | Week2 测试可运行 | `RESOURCES` 的 modules：import 为什么有副作用？ | 写职责/依赖图 | P1；ADR+3 个行为测试 | 只标入口函数，不移动代码 |
| B2 纯模块 | 把框架/模型从规则中隔离 | B1 ADR | pytest monkeypatch：何时不用 patch？ | 抽 router/calculator | P2；新旧 import/输出一致 | 复制后导入，绿再删除旧代码 |
| B3 类型 contract | 让非法状态早被发现 | B2 单测绿 | typing/mypy：`Literal` 比 str 多什么？ | `TaskType`、状态注解 | P3；故意错误被 mypy 捕获 | 只检查两个纯模块 |
| B4 输入边界 | 用户输入不应绕过业务规则 | B3 类型范围明确 | `RESOURCES` 的 pytest：参数化为何有用？ | plan/calculator 边界表 | P4；6 个边界 test | 先定义 reject/limit，不急实现 tool |
| B5 质量门 | 防止下周把本周改坏 | B1–B4 全绿 | Ruff/pre-commit：各检查什么？ | 一条 quality command | P5；四工具各抓住一类错 | 逐条运行、记录首个失败 |

**本周连接练习**：P6 把 B1–B5 的证据编进审计报告。通过条件不是“文件数量增加”，而是能从一次 router 输入解释到 test、type、lint 各自的保护作用。
