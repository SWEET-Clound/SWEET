# 第 1 周资料清单与阅读边界

本周资料遵循“够用即停”。总阅读时间控制在 3 小时以内，剩余时间用于运行、测试和解释。

| 优先级 | 资料 | 阅读范围 | 学完必须能做什么 | 产出 |
| --- | --- | --- | --- | --- |
| 必读 | [Python on Windows](https://docs.python.org/3/using/windows.html) | 安装与命令发现 | 安装并定位解释器 | 版本和路径证据 |
| 必读 | [venv](https://docs.python.org/3/library/venv.html) | 创建、激活、不可移动 | 创建并验证隔离环境 | 重建命令 |
| 必读 | [WSL install](https://learn.microsoft.com/en-us/windows/wsl/install) | install、status、list | 安装并证明 WSL2 | `wsl -l -v` 输出 |
| 必读 | [CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/) | 3.1–3.3 | 验证 GPU，避免装错驱动 | `wsl nvidia-smi` |
| 必读 | [pytest Get Started](https://docs.pytest.org/en/stable/getting-started.html) | assert、raises、discovery | 写并运行基本测试 | 五种测试技巧 |
| 必读 | [pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html) | setattr/setenv/undo | 隔离环境和模型边界 | monkeypatch 示例 |
| 选读 | [unittest.mock](https://docs.python.org/3/library/unittest.mock.html) | Mock、side_effect、assert | 模拟失败并检查调用 | mock 概念卡 |
| 必读 | [Logging HOWTO](https://docs.python.org/3/howto/logging.html) | level/logger/format | 设计最小请求日志 | trace 与敏感字段表 |
| 原则参考 | [LangGraph test](https://docs.langchain.com/oss/javascript/langgraph/test) | fresh graph/checkpointer、node test | 解释测试隔离 | 两条原则笔记 |
| 原则参考 | [Evaluate graph](https://docs.langchain.com/langsmith/evaluate-graph) | e2e/intermediate/node | 区分三个评测层级 | 评测层级图 |
| 选读 | [Pro Git：记录变更](https://git-scm.com/book/en/v2/Git-Basics-Recording-Changes-to-the-Repository) | status/diff/stage/commit | 读懂工作树状态 | Git 命令卡 |

## 阅读方法

每份资料最多写四项：

1. 一句话核心概念；
2. 当前项目中的对应位置；
3. 一个可以运行的最小例子；
4. 一个仍不理解的问题。

禁止整页摘抄。能搜索到的 API 细节不需要背诵。

## 本周不读

- 完整 LangGraph/LangChain 教程；
- CUDA 编程指南；
- SGLang 源码；
- Docker/Kubernetes；
- pytest 插件生态；
- 日志框架对比；
- MLOps 课程。

这些内容并非不重要，而是不能帮助本周硬门槛。
