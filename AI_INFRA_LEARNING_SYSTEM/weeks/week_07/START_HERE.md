# Week 7 从这里开始

本周要学习的不是某个观测平台按钮，而是“一个失败发生时，哪些最小信息能让我定位、分类、验证修复”。

进入 Gate：Week 6 的 raw load 数据、queue/admission policy 都存在。先画 trace contract，再加 span；不能先安装一堆 SDK 再猜应该看什么。

每个故障必须有：触发方法、公开响应、trace 信号、日志字段、自动测试、是否可重试。缺一项不算完成。
