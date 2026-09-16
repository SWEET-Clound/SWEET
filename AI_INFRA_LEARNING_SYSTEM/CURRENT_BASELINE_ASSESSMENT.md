# 当前仓库能力基线审计

> 审计对象：`D:\Code\SWEET`  
> 日期：2026-09-16  
> 说明：这里只依据仓库中可见证据，不推断个人没有展示的能力。

## 已有优势

### 1. 已进入 Agent 基本概念

仓库中已有：

- `langgraph_demo.py`：最小 StateGraph；
- `Agents/study_assistant.py`：状态、路由、工具、记忆、流式和错误处理方向；
- `Agents/CalculateAgent/calculate_agent.py`：独立 Agent 练习。

这意味着起点不是完全不了解 Agent，可以直接把精力从“再做一个聊天 Demo”转向工程化、评测和 serving。

### 2. 量化项目体现出较强实验纪律意识

`quant_demo/LEARNING.md` 和 `TECHNICAL.md` 已经讨论：

- 前视偏差；
- holdout；
- 交易成本和滑点；
- 压力测试；
- 原子缓存；
- 幂等账本；
- 保留失败结果；
- 机械门槛不代替人工判断。

这些思想可以直接迁移到 AI Infra：固定 workload、训练/评测隔离、报告失败实验、原始数据可审计、不给单次 benchmark 过度解释。

### 3. 已有主动学习轨迹

Git 历史显示从基础练习逐步进入 LangGraph 和量化方案，说明当前适合建立长期主线，而不是再增加互不相干的 Demo。

## 当前可见缺口

### 工程化

- Agent 主要仍为单文件原型；
- 未发现系统化 pytest、类型检查、CI 和依赖锁定证据；
- 尚无清晰 package/API/配置边界；
- 缺少真实并发、取消、超时、背压和资源释放验证；
- `calculator` 使用受字符限制的 `eval`，仍应替换成 AST/专用解析器；
- 尚未看到可审计的模型评测集和回归门禁。

### 模型与推理系统

- 尚未看到 PyTorch 模型实现；
- 尚未看到 KV Cache、batching、量化或 profiler 实验；
- 尚未看到本地模型 serving；
- 尚未看到 SGLang/vLLM 运行证据；
- 尚未看到 GPU、CUDA、Triton 或分布式系统实践。

### 产品与外部验证

- 尚未看到实时音视频系统；
- 尚未看到真实用户、SLO 或长期运行；
- 尚未看到上游 Issue/PR；
- 尚未看到公开 benchmark 被第三方复现。

这些不是负面评价，而是路线必须从工程地基开始、不能直接跳到 CUDA kernel 的依据。

## 初始能力评级（只依据仓库证据）

| 能力 | 暂定证据等级 | 依据 | 下一证据 |
| --- | ---: | --- | --- |
| Python 基础 | E2 | 多个可运行脚本 | 包结构、类型、测试、异步服务 |
| Agent 编排 | E2 | LangGraph 状态与工具 | 固定评测、并发、恢复 |
| 实验纪律 | E2–E3 | 量化文档和实现 | AI benchmark 原始数据与复现 |
| PyTorch/Transformer | E0–E1 | 无充分仓库证据 | Mini inference lab |
| 推理系统 | E0 | 无充分仓库证据 | 双引擎 benchmark |
| 实时多模态 | E0 | 无充分仓库证据 | LiveKit 端到端项目 |
| 开源协作 | E0–E1 | 有个人 Git 仓库 | 上游 Issue/PR/review |
| 生产可靠性 | E0–E1 | 量化项目有部分意识 | SLO、故障注入、7 天运行 |

## 最合理的起步顺序

```text
当前 Agent 工程化
→ asyncio/网络/可观测性
→ PyTorch/Transformer/KV Cache
→ serving benchmark
→ SGLang 源码与贡献
→ 实时语音 Agent
→ 可靠性
→ Agent/RL KV Cache 专业化
```

## 第一次基线更新

请完成 `templates/BASELINE.md`。上表只是仓库审计，个人自测和实际运行结果应覆盖它。完成后将本文件升级为 v1.1，并标注哪些判断被新证据修正。
