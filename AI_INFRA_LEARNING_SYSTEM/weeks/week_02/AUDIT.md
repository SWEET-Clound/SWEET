# Week 2 计划审核与修订

审核日期：2026-10-03。审核对象：当前 `Agents/study_assistant.py`、Week 1 证据与 Week 2 原计划。

## 审核结论

第二周主题正确：它直接对应当前项目的三个真实问题——顶层模型初始化阻碍测试、计算器使用 `eval`、缺少独立的应用依赖/启动边界。

| 审核点 | 原风险 | 修订规则 |
| --- | --- | --- |
| 时间 | 标 10 小时，实际为 10 小时 20 分 | W2-6 改为 1h10m，核心总计恰 10 小时 |
| PATH/venv | 默认 Python 可运行 | W2-0 增加解释器 Gate 和非破坏恢复分支 |
| 路由测试 | 直接 import 主文件会触发 provider 依赖 | 先写纯 `Agents/routing.py`，测试只导入它 |
| `eval` 替换 | AST 的接受范围不够精确 | 固定只允许数字、括号、`+ - * /`、一元正负；拒绝 `**` |
| 依赖版本 | 容易凭感觉填写版本 | 只有真实安装和运行后才记录版本 |

## 当前环境 Gate：已通过

审核过程中曾有一个新启动的 PowerShell 会话出现：

```text
python                → 未被识别
.venv\Scripts\python → 无法创建进程
```

这不否定你在某一个已经打开的终端里修改 PATH 成功；已启动进程不会自动继承后续的 Windows 环境变量更新。随后用户在同一 PowerShell 中完成了如下验证：

```text
工作目录：D:\Code\SWEET
python --version                              → Python 3.14.7
python -c "import sys; print(sys.executable)" → C:\Users\Admin\AppData\Local\Python\pythoncore-3.14-64\python.exe
.venv\Scripts\python.exe --version           → Python 3.14.7
.venv\Scripts\python.exe -m pytest -q practice\test_grade_detect.py → 1 passed in 0.01s
```

结论：PATH、基础解释器、项目 `.venv` 与 pytest 入口检查均通过。之前 `.venv` 的“找不到”是因为命令在 `C:\Windows\System32` 下执行，相对路径指向了错误位置；不是虚拟环境损坏。

未来若在新终端遇到同类问题，先在项目根目录执行开始页的三条解释器命令。决策树如下：

```text
python 可运行
  ├─ .venv 可运行 → 跑 pytest，进入 W2-1（当前路径）
  └─ .venv 失败   → 先新建 .venv-rebuild-check 验证，不覆盖旧环境
python 不可运行
  └─ 重开终端；仍失败则修复基础 Python 安装/权限
```

## 精确的文件变更边界

| 顺序 | 新增/修改 | 单一责任 | 最低验证 |
| --- | --- | --- | --- |
| 1 | `Agents/__init__.py` | 明确 package 边界 | Python 可定位模块 |
| 2 | `Agents/config.py` | 读取并校验一个环境变量 | 3 个 `monkeypatch` 测试 |
| 3 | `Agents/routing.py` | 纯输入到任务类型规则 | 5 个路由测试 |
| 4 | `Agents/safe_calculator.py` | 有限 AST 算术 | 8+ 测试，无 `eval` |
| 5 | `Agents/errors.py` | 错误类别到公开文案 | 4 类错误测试 |
| 6 | `Agents/study_assistant.py` | 只接入已验证模块 | 原接口保留；不重写 graph |
| 7 | `tests/` | 记录每个契约 | `pytest -q` 离线通过 |
| 8 | `.env.example`、启动说明 | 复现和秘密边界 | 新终端按文档验证 |

每完成一行，都先运行该行的最小测试；不要攒到最后一起排错。

## 每板块的输入、输出与停止条件

| 板块 | 开始前必须有 | 结束时必须交出 | 何时停止并求助 |
| --- | --- | --- | --- |
| W2-0 | 新终端、无真实 Key 输出 | Python 路径、pytest 输出、解释 | 基础 Python 无法运行 |
| W2-1 | 可阅读现有代码 | 8 行副作用地图、失败预测 | 分不清定义和调用 |
| W2-2 | E2 行为表 | `config.py`、3 个绿测 | 测试读取真实 Key |
| W2-3 | E3 行为表 | AST 函数、8 个绿测、无 `eval` | 想复制陌生完整实现 |
| W2-4 | 四类错误表 | 4+ 错误测、5+ 路由测、trace | 为测试启动真实模型 |
| W2-5 | 已通过的离线测试 | 依赖清单、`.env.example`、新终端验证 | 未验证就猜版本 |
| W2-6 | 前五项证据 | 全量结果、口试、复盘 | 用“能跑”替代证据 |

## 依赖顺序审核

```text
W2-0 环境可信
    ↓
W2-1 看见副作用
    ↓
W2-2 配置可测 ─┐
W2-3 算术可测 ─┼→ W2-4 错误与纯路由 → W2-5 复现 → W2-6 验收
                    ┘
```

W2-2 与 W2-3 可以互换；其余不建议改变。尤其 W2-4 必须在至少一个纯模块存在后再做，否则容易把 mock 当成绕过设计问题的工具。

## 本周不应作出的结论

- 10 个离线测试通过，不代表真实 provider 可用；
- AST 计算器安全，不代表整个 Agent 达到生产安全；
- 创建 `__init__.py` 不等于完成第 3 周的架构拆分；
- PATH 可用不等于 `.venv` 必然可用；
- 锁定版本不等于永远不升级，只是让一次已知可用结果能重现。
