# Week 2 从这里开始：把 Demo 变成可验证的小工程

本周唯一问题：**在陌生或缺少配置的环境中，你能否让当前 Agent 的基础逻辑安全地启动、清楚地失败，并证明关键规则没被改坏？**

这不是新框架周。你会先理解当前单文件 Agent 的边界：

```text
用户输入
  ├─ 纯逻辑：路由、算术、错误文案 → 输入相同，结果应相同，可离线测试
  └─ 外部边界：环境变量、模型、网络 → 可能缺失/失败，应明确处理
```

没有 API Key、网络或真实模型时仍能测试大部分逻辑，这就是本周的价值。

## 文件导航

| 文件 | 用途 | 何时使用 |
| --- | --- | --- |
| [README.md](README.md) | 六个板块的完整路径 | 开始每个板块前 |
| [KNOWLEDGE_MAP.md](KNOWLEDGE_MAP.md) | 概念的白话词典 | 遇到术语时 |
| [EXERCISES.md](EXERCISES.md) | 练习与预期结果 | 动手时 |
| [WORK_LOG.md](WORK_LOG.md) | 记录预测、命令和证据 | 每次结束时 |
| [RUBRIC.md](RUBRIC.md) | 本周验收标准 | 周末 |
| [AUDIT.md](AUDIT.md) | 计划审核、依赖顺序和停止条件 | 开始前与卡住时 |
| [SESSION_RUNBOOK.md](SESSION_RUNBOOK.md) | 五次学习会话的分钟级执行卡 | 每次学习时 |

## 20 分钟入口检查

Week 1 未完全结束也可以进入 Week 2，但先验证解释器，再验证测试：

```powershell
Get-Command python -All
python --version
python -c "import sys; print(sys.executable)"
```

预期：第一条显示实际 `python.exe` 路径；第二条显示版本；第三条显示同一个解释器。不要只看 PATH 文本，必须以这三条命令的结果为准。

接着运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q practice\test_grade_detect.py
```

在 `WORK_LOG.md` 记录命令和输出，再不看资料回答：`assert` 证明什么？又没有证明什么？

合格回答类似：它证明列出的输入有预期输出；没有证明未列出的输入、真实模型调用或整个项目都正确。

若命令失败，先记录完整错误并解决环境问题；若解释不顺，回看 Week 1 的 `LESSON_01_PYTEST.md` 15 分钟即可。

### 入口失败分支

| 现象 | 含义 | 本周只做什么 |
| --- | --- | --- |
| `python` 找不到 | 新终端尚未继承 PATH，或 PATH 项错误 | 关闭并重新打开终端后复测；记录 `Get-Command` 输出 |
| `python --version` 报拒绝访问 | 基础 Python 安装/权限异常 | 停止创建 venv，先修复基础 Python |
| 基础 Python 正常、`.venv` 失败 | venv 指向的基础解释器改变或环境损坏 | 先建不覆盖的 `.venv-rebuild-check` 验证，不删旧 `.venv` |
| pytest 失败、解释器正常 | 测试或依赖问题 | 记录完整输出，再定位 |

虚拟环境是可重建的隔离目录，但本周遵循“先新建验证、不直接删除”的恢复策略。[官方 venv 文档](https://docs.python.org/3/library/venv.html)也明确说明它应可被重建。

## 本周不做

- 不以真实 DeepSeek 调用作为测试；
- 不学 Docker、HTTP、SSE、asyncio、SGLang、CUDA；
- 不重写完整 LangGraph 图；
- 不将 API Key 写进代码、测试、截图或 Git。

## 本周结束时的证据

1. 一张当前 Agent 代码地图；
2. 可测试的配置检查；
3. 不含 `eval` 的安全算术计算器；
4. 对空输入、缺少 Key、非法算式和依赖失败的明确契约；
5. 至少 10 个不联网、不用真实 Key 的测试；
6. 从新终端复现离线测试的说明。
