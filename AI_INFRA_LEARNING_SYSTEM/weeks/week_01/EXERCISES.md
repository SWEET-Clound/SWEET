# Week 1 练习册：环境、pytest 与验证思维

> 这是对 README 的可检查版本。你已经完成的项目可填写真实证据；不要为了“补文档”重装环境。

| 题目 | 输入/动作 | 预期与验收 | 证据 |
| --- | --- | --- | --- |
| E1 Python 解释器 | 在项目根目录运行 `python --version`、`python -c "import sys; print(sys.executable)"` | 版本与解释器路径明确；不是 Windows Store 占位程序 | `ENVIRONMENT_EVIDENCE.md` |
| E2 venv 边界 | 进入项目根目录运行 `.\.venv\Scripts\python.exe --version` | 路径存在且版本正确；在 `C:\Windows\System32` 失败是预期路径错误，不是 Python 故障 | 终端输出 |
| E3 pytest 思维 | 为一个纯函数写正常、边界、错误三例 | 先有至少一个红测，修复后全绿 | 测试名与输出 |
| E4 可重复命令 | 运行 `.\.venv\Scripts\python.exe -m pytest -q practice\test_grade_detect.py` | 退出码 0；记录通过数，不只截图 | `CURRENT_STATUS.md` |
| E5 失败分类 | 任选一次路径/导入/断言失败 | 写“命令、第一条错误、原因、修复、如何防回归” | `FAILURE_CATALOG.md` |

**完成条件**：E1–E4 均有真实命令证据；E5 至少一条真实或明确标注为待遇到的失败模式。
