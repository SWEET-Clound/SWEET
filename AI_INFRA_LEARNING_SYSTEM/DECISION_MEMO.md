# 技术方向决策备忘录：为什么是推理系统生态，而不是单押一个框架

> 决策日期：2026-09-16  
> 复审日期：2026-12-16  
> 决策状态：有条件加码  
> 当前载体：SGLang  
> 不可替代性结论：尚未由个人外部成果证明，必须通过 Gate 逐级验证

## 1. 结论分成两个不同命题

### 命题 A：掌握 SGLang 本身会让人不可替代

**否定，置信度超过 90%。**

原因是 API、部署参数、标准 benchmark、普通模型适配和部分 kernel 调优正在被云平台、编译器和代码代理自动化。只熟悉一个仓库的当前实现，折旧会很快。

### 命题 B：以 SGLang 为实验主线，形成跨引擎/跨硬件推理、正确性与性能评测、生产可靠性和高约束行业场景的能力组合

**值得投入。** 对该能力组合未来 3–5 年仍具有高价值与迁移性的判断接近 90%；对“某个具体学习者最终形成不可替代地位”的判断不能预先给 90%，只能由 6–18 个月的外部验证逐步提高。

因此，本计划不是信仰承诺，而是一套便宜证伪、逐级加码的决策系统。

## 2. 选择 SGLang 作为当前载体的证据

### 2.1 技术覆盖面

SGLang 当前覆盖：

- RadixAttention、Paged Attention、连续批处理和 chunked prefill；
- prefix cache、结构化输出和投机解码；
- tensor/pipeline/expert/data parallelism；
- Prefill/Decode disaggregation；
- 大语言、多模态、embedding、reward 和 diffusion 模型；
- RL rollout 与权重更新；
- NVIDIA、AMD、TPU、Ascend 等硬件。

来源：

- https://github.com/sgl-project/sglang
- https://docs.sglang.io/
- https://github.com/sgl-project/sglang/blob/main/docs/docs/advanced_features/sglang_for_rl.mdx

这意味着一个主项目可以同时连接模型、runtime、调度、GPU、网络、RL 和多模态，而不是把学习限制在 API 编排层。

### 2.2 活跃度和前沿性

2026-09-16 的近期提交同时涉及 KV shard、NIXL、AMD 和 DeepSeek-V4.1 通信内核：

- KV shard：https://github.com/sgl-project/sglang/commit/e7f744733333a5ebb63f1114d9632bfca7079a4a
- NIXL：https://github.com/sgl-project/sglang/commit/7b7620774c1debbb274b28d6dcec33a25be9bae7
- AMD：https://github.com/sgl-project/sglang/commit/a3bf25dc620f31fc672aeced1465d6fe7c81d28f
- DeepSeek-V4.1：https://github.com/sgl-project/sglang/commit/7d5696b3a1638a7c980e46ed77eb876faad158a2

路线图仍在推进大规模并行、KV cache、RL、多模态和下一代硬件：

- https://github.com/sgl-project/sglang/issues/22949

这些是“当前仍处于高强度工程演化”的证据，但不是未来成功的保证。

### 2.3 Agent/RL 工作负载正在产生新的系统问题

Agent 多轮、分叉、工具调用、长上下文和重复系统前缀，使 KV Cache 从单进程优化变成跨服务状态管理问题。SGLang 的公开工作已经涉及 session identity、session cache、KV hint、分层缓存和 admission/telemetry/expiry：

- Agentic KV Cache 路线图：https://github.com/sgl-project/sglang/issues/27574
- P/D Disaggregation 路线图：https://github.com/sgl-project/sglang/issues/21703

RL rollout 还引入训练—推理 kernel/batching 不一致造成 logprob 漂移、权重热更新、容错和资源共置/分离等问题：

- SGLang RL 文档：https://github.com/sgl-project/sglang/blob/main/docs/docs/advanced_features/sglang_for_rl.mdx
- slime：https://github.com/THUDM/slime
- slime 故障恢复：https://github.com/THUDM/slime/blob/main/docs/en/advanced/fault-tolerance.md

这类问题连接请求生命周期、缓存驻留、session affinity、backpressure、分布式一致性、SLO 和数值正确性，是本计划把 Agent/RL KV Cache 选为第一专长的主要依据。

### 2.4 对外部贡献相对开放

官方贡献指南明确给出：

- CPU unit test；
- E2E test；
- 正确性评测；
- benchmark/profiling；
- docs/cookbook；
- AOT/JIT kernel；
- DeepGEMM/DeepEP；
- CI 和 review 流程。

来源：https://github.com/sgl-project/sglang/blob/main/docs/docs/developer_guide/contribution_guide.mdx

新人因此可以从测试、文档、parser、metrics 逐步进入 scheduler/cache/kernel，而不必一开始就拥有多节点 GPU。

## 3. 主要替代力量

## 3.1 云平台吞掉部署与常规调优

- Azure Managed Compute 管理 GPU 拓扑、runtime、镜像和补丁：  
  https://learn.microsoft.com/en-us/azure/foundry/concepts/managed-compute-overview
- AWS 可以分析模型和 workload，在真实 GPU 上尝试部署配置并给出推荐：  
  https://docs.aws.amazon.com/sagemaker/latest/dg/generative-ai-inference-recommendations.html

结论：会部署、会填参数、会跑标准吞吐测试不足以形成长期价值。

## 3.2 编译器吞掉局部手写优化

- `torch.compile` 使用 tracing 和 Inductor 自动生成、缓存优化代码：  
  https://docs.pytorch.org/docs/stable/generated/torch.compile.html
- XLA GPU 管线从 StableHLO 到 TritonIR/PTX：  
  https://openxla.org/xla/gpu_architecture
- Triton 本身就在提高 kernel 开发抽象层：  
  https://triton-lang.org/main/programming-guide/chapter-1/introduction.html

结论：仅仅“会写一个 Triton kernel”也不等于不可替代。需要理解编译器、硬件、正确性和真实 workload。

## 3.3 代码代理吞掉可自动评分的优化

AlphaEvolve 已展示自动搜索算法和 kernel 优化：  
https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/

结论：可被清晰 evaluator 自动评分的问题，会越来越多交给代理搜索。人的优势转向 evaluator 设计、问题边界、线上归因、风险和责任。

## 3.4 标准化削弱单一引擎知识

- NVIDIA Dynamo 明确支持不同 inference backend：  
  https://docs.nvidia.com/dynamo/
- Kubernetes Gateway API Inference Extension 抽象模型服务和路由：  
  https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/main/site-src/guides/implementers.md

结论：SGLang 专属 CLI 和配置知识会折旧；跨引擎方法、评测资产和生产经验更耐久。

## 4. 商品化风险判断

以下区间是基于上述趋势的主观推断，不是统计概率：

| 能力 | 未来 2–3 年明显商品化风险 |
| --- | ---: |
| 安装部署、OpenAI API、常规 K8s 清单 | 85–95% |
| 标准 TTFT/TPOT/tokens/s 测试与扫参 | 80–95% |
| 普通模型 adapter/day-N 支持 | 70–90% |
| 常规量化、投机解码和 autoscaling 配置 | 75–90% |
| 例行 Triton/kernel 移植 | 60–80% |
| 单一引擎故障排查 | 65–85% |
| 新模型 day-0 跨硬件支持 | 35–55% |
| 跨 kernel/runtime/network/scheduler 尾延迟归因 | 30–50% |
| 带故障注入的正确性、恢复和容量工程 | 25–45% |
| 绑定行业约束的数据/评测/SLO/成本闭环 | 20–40% |
| 创造并生产采用的新调度/缓存/推理算法 | 15–35% |

学习时间必须更多投向表格下半部分。

## 5. 为什么仍然值得投入

生产推理没有通用最优解。例如 P/D 分离并不在所有模型、prompt 长度、并发量和网络条件下优于聚合部署；选择边界依赖真实流量与硬件：

- https://docs.nvidia.com/dynamo/dev/kubernetes/disaggregated-serving/overview

SGLang 自带 benchmark 已能输出 TTFT、ITL、TPOT 和尾延迟，因此“会跑 benchmark”并不稀缺：

- https://docs.sglang.ai/developer_guide/bench_serving

真正稀缺的是：

- 设计没有明显偏差的 workload；
- 对结果给出因果解释；
- 同时验证质量、正确性、稳定性和成本；
- 把实验改进转化为真实线上收益；
- 在新模型或新硬件出现时迁移方法。

## 6. 加码 Gate 与置信度阶梯

只有通过 Gate，才允许提高“这条路线适合我”的个人置信度。

### Gate 0：第 6–8 周，基础适配

要求：

- 同一小模型在至少两个后端完成可复现实验；
- 重复实验报告分布或置信区间，而非只跑一次；
- 用 profiler 指出至少一个瓶颈，并解释为何不是测量噪声；
- 仍愿意继续读源码、查并发问题和做性能测量。

通过后：个人路线置信度可提升到 65–70%。  
未通过：转向上层 AI 产品或评测工程，不继续重押底层 Infra。

### Gate 1：第 3–4 月，独立工程能力

要求：

- 建立 correctness + latency + cost 三维 benchmark；
- 能稳定复现至少一个性能回归；
- 完成一个非纯文档的上游 PR，或获得维护者对技术方案的明确认可；
- 能讲清楚一次请求从 API 到 scheduler/model runner 的路径。

通过后：个人路线置信度可提升到 70–78%。

### Gate 2：第 6–8 月，跨层收益

要求：

- 在真实或高保真 trace 上，使 P95/P99 或单位成本改善至少 15%；
- 质量、错误率和正确性不退化；
- 在第二引擎或第二硬件上解释为何收益迁移或失效；
- 公开原始数据和复现方法。

通过后：个人路线置信度可提升到 78–85%。

### Gate 3：第 9–12 月，外部需求

满足至少一项：

- 有外部团队持续使用工具、数据集或方案；
- 有两个实质上游 PR 被合并；
- 获得付费试点、实习或合作；
- 真实产品有持续用户并积累不可公开但可审计的工作负载。

同时通过 Gate 2 和 Gate 3，才有根据把“方向适配与市场拉力”的个人主观信心提高到约 85–90%。

### Gate 4：第 12–18 月，护城河雏形

要求：

- 拥有别人难以快速复制的 workload、trace、评测集或硬件经验；
- 处理过一次真实事故、容量或质量问题；
- 方法可以迁移到新模型、新硬件或新引擎；
- 有外部采用证据。

通过后，才可以谨慎使用“短期难替代”描述自己的能力。

## 7. 项目比较与当前排序

评分不是受欢迎程度，而是对本计划目标的适配度。

| 项目 | 前沿/空间 | 技能迁移 | 稀缺潜力 | 当前可进入性 | 主要用途 |
| --- | ---: | ---: | ---: | ---: | --- |
| SGLang | 10 | 9 | 9 | 6 | 主实验场 |
| vLLM | 9 | 10 | 8 | 6 | 原理与对照 |
| LiveKit Agents | 8 | 8 | 7 | 9 | 实时产品入口 |
| LeRobot | 10 | 9 | 10 | 4 | 远期第二曲线 |
| OpenHands | 8 | 7 | 6 | 8 | 软件 Agent 架构参考 |
| LangGraph | 7 | 6 | 4 | 10 | 上层编排工具 |

因此不是“只学 SGLang”，而是：SGLang 深挖、vLLM 交叉验证、LiveKit 落地、LeRobot 保留期权。

## 8. 决策规则

继续加码的证据：

- Gate 按期通过；
- 上游有正向技术反馈；
- 真实用户或团队拉动新需求；
- 能将方法迁移到第二引擎/硬件；
- 对调试和实验仍有持续兴趣。

降低投入或转向的证据：

- 8 周后仍只能复制教程；
- 3–4 个月无法建立可信 benchmark；
- 6 个月三次贡献尝试均无技术反馈，也没有用户；
- 自动调优在目标 workload 上已达到个人方案 10% 以内，且没有数据、法规或硬件优势；
- 主项目连续两个季度明显衰退；
- 个人长期厌恶 profiler、并发 bug 和源码调试。

最终原则：**90% 不是对未来喊出的口号，而是通过 Gate 让错误方向尽早暴露，并只在外部证据持续出现时逐级加码。**
