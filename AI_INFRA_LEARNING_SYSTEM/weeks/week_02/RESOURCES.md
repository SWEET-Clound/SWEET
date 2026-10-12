# Week 2 定向资料索引

每项只读到能完成对应练习为止；回答问题并留下产物，不能以收藏链接代替学习。

| 资源 | 只读范围 | 阅读问题 | 对应产物 |
| --- | --- | --- | --- |
| [Python `os.environ`](https://docs.python.org/3/library/os.html#os.environ) | 环境变量读取 | 为什么配置不应写死？缺失值如何分类？测试如何隔离？ | config tests |
| [Python `ast`](https://docs.python.org/3/library/ast.html) | parse、节点、visitor | 为什么 AST 比 `eval` 安全？allowlist 在哪里？未知节点怎样处理？ | safe calculator |
| [pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html) | 参数化测试 | 什么边界应压缩成参数表？测试名如何可读？ | calculator cases |
| [pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html) | 环境/依赖替换 | 何时使用 monkeypatch？为什么测试结束后自动还原重要？ | config isolation |
| [Twelve-Factor config](https://12factor.net/config) | config 原则 | secret 与普通 config 有何不同？哪些绝不写进日志？ | error/redaction policy |

停止线：本周不读部署、Docker、异步或 SGLang；先完成配置与安全计算的离线测试。
