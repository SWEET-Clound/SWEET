# Week 3–12 实验脚手架与环境门

当前真实工作区尚未拥有后续周会用到的 `tests/`、`labs/`、`scripts/`、`workloads/` 等目录。它们是**在对应周由学习者创建的产物**，不是预设已经存在的代码。本文件定义唯一创建顺序，避免计划引用不存在的文件而让人无从开始。

## Week 3：先建立可测试的 Agent 结构

从项目根目录执行目录创建（只创建空目录，不覆盖已有文件）：

```powershell
New-Item -ItemType Directory -Force tests, scripts, adr, experiments\week_03 | Out-Null
New-Item -ItemType Directory -Force tests\fixtures | Out-Null
```

然后按 Week3 P1/P2 从当前 `Agents/study_assistant.py` 抽出**一个**无 I/O 的函数并先写 test。现有入口若在 import 时初始化模型/graph，首要练习是将初始化移动至 factory/`main()`；在此之前，不执行“import 无副作用”验收。

## Week 4–8：服务与 workload 结构

```powershell
New-Item -ItemType Directory -Force docs, evidence, reviews, workloads\agent_cache_lab, experiments\week_04, experiments\week_05, experiments\week_06, experiments\week_07 | Out-Null
```

依赖安装不是默认前提。每周 Start Here 先执行 `python -m pip show <package>`；缺依赖时记录版本/失败，再以项目明确的开发依赖文件安装。FastAPI、pytest-asyncio、OpenTelemetry 只在对应周进入环境门；没有通过环境门时使用 pure-function/fake 路径，不宣称集成已验证。

## Week 9–12：独立模型实验室

```powershell
New-Item -ItemType Directory -Force labs\tensor_lab\src, labs\tensor_lab\tests, labs\tensor_lab\experiments\raw, labs\tensor_lab\experiments\profiler | Out-Null
New-Item -ItemType Directory -Force labs\attention_lab\src, labs\attention_lab\tests, labs\attention_lab\docs, labs\attention_lab\experiments | Out-Null
New-Item -ItemType Directory -Force labs\decoder_lab\src, labs\decoder_lab\tests, labs\decoder_lab\experiments\raw, labs\decoder_lab\experiments\profiler | Out-Null
```

PyTorch 只可在一个独立、明确记录 Python/OS/device 的实验环境安装。先确认官方 wheel 对当前 Python 版本的支持；CPU FP32 为每项正确性测试的强制 fallback。CUDA 不可用时，GPU benchmark/profile 只能标 `UNVERIFIED`，不能伪造数值。

## 每周环境门模板

在 Work Log 填写：解释器路径、`python --version`、依赖版本、OS、torch device（如适用）、测试命令和 exit code。下列任何情况都先停在环境门：从错误目录调用 venv、依赖来自全局环境、模型下载混入单元测试、CUDA/WSL/Windows 环境被混为同一结论。
