# Week 1 当前状态

> 核验日期：2026-10-03  
> 状态来源：实际命令输出、仓库文件、用户基线  
> 下一检查点：完成 `LESSON_01_PYTEST.md` 行为表并进行参数化测试练习

## 环境核验

| 项目 | 结果 | 状态 | 说明 |
| --- | --- | --- | --- |
| Windows Python | 3.14.7 | 通过 | 当前 PowerShell 的 `python` 已能定位并启动 |
| 项目 `.venv` | Python 3.14.7，隔离为 True | 通过 | 应始终使用 `.venv\Scripts\python.exe` |
| pip | 26.2.1 | 通过 | `.venv` 内可用 |
| pytest | 9.1.1 | 通过 | 由 `requirements-dev.txt` 固定并安装 |
| WSL | Ubuntu，WSL2 | 通过 | 当前为 stopped 属正常按需启动状态 |
| WSL Python | 3.14.4 | 通过 | `/usr/bin/python3` |
| WSL pip | 未安装 | 待处理 | Week 1 Windows 测试不依赖它 |
| WSL GPU | RTX 5070 Ti 16303 MiB | 通过 | 驱动 591.86 |
| WSL Linux NVIDIA driver | 未发现 | 通过 | 符合 WSL 使用 Windows 驱动的方式 |
| Git | 2.55.0.windows.5 | 通过 | 根目录 `.gitignore` 已建立 |

## 学习核验

| 内容 | 当前证据 | 状态 |
| --- | --- | --- |
| pytest 基础概念 | 用户已学习；存在一个简单 assert 测试 | 初步完成 |
| pytest 在项目运行 | `practice/test_grade_detect.py`：`1 passed in 0.01s`（2026-10-03 再验证） | 已完成基础验证 |
| 测试边界设计 | 尚未对 Agent 建立离线测试 | 未完成 |
| Agent 路由理解 | 有 LangGraph 代码，但无独立解释/测试证据 | 待巩固 |
| trace 理解 | 模板存在，用户不清楚作用 | 正在补充 |
| 失败模式分析 | 模板未填写 | 未开始 |

## 已发现的关键工程问题

1. `study_assistant.py` 在模块导入时初始化模型和 graph；
2. 这使纯路由测试也依赖 LangChain/LangGraph/OpenAI 包和模型配置；
3. 项目根目录已有最小 `requirements-dev.txt`，但尚无应用依赖清单和测试配置；
4. `.venv` 已有 pytest，但尚无 LangGraph/LangChain 应用依赖；
5. `practice/test_grade_detect.py` 把被测函数和测试写在同一文件，可以学习，但不是长期项目结构；
6. `grade_detect` 对大于 100、负数和非整数的契约没有被明确设计；
7. 用户当前最大的缺口不是语法，而是模块边界、测试边界和设计理由。

## 下一步顺序

1. 完成 `LESSON_01_PYTEST.md` 的行为表和口头问题；
2. 用 `grade_detect` 完成一次 Red–Green–Refactor 和参数化测试练习；
3. 建立 Agent 路由的最小离线测试接缝；
4. 完成 5 个 `router_node` 行为测试；
5. 再用 `TRACE.md` 解释一条 `local_knowledge` 路径；
6. 最后填写失败目录。

不跳过第 2 步直接进入 Agent 测试，因为当前目标是先掌握“为什么测”，再掌握“怎么 mock”。
