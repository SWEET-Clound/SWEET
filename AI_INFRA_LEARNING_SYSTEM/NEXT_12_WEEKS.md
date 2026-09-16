# 第一个 12 周执行计划

> 默认每周 15 小时。每周只有一个主目标。  
> 每周完成后复制 `templates/WEEKLY_REVIEW.md`，以 `reviews/YYYY-Www.md` 命名。  
> 任何任务未通过验收时，延长当前周，不带病进入下一阶段。

## 第 1 周：建立权威基线

主问题：现在的环境和能力能否被客观描述？

任务：

- 完成 `templates/BASELINE.md`；
- 记录 CPU、内存、GPU、驱动、OS、Python；
- 为 `study_assistant.py` 写下 10 个可观察失败模式；
- 建立 `reviews/`、`experiments/`、`adr/` 目录；
- 给现有 Agent 增加至少 5 个不调用真实模型的测试；
- 记录一次完整请求的日志和时序。

Definition of Done：基线文件无空白核心字段；5 个测试可重复运行；已创建第一份周报。

## 第 2 周：配置、安全和复现

主问题：陌生环境能否可靠启动当前 Agent？

任务：

- 集中管理配置并在启动时校验；
- 替换 `eval` 计算器；
- 固定直接依赖版本；
- 明确模型调用失败、工具失败和无输入路径；
- 写一份从干净环境启动的 README；
- 增加错误路径测试。

Definition of Done：另一干净环境可按文档启动；缺少密钥时快速、清晰失败；不存在 `eval`；至少 10 个测试。

## 第 3 周：包结构、类型和质量门禁

主问题：代码是否能被安全修改而不依赖单文件上下文？

任务：

- 拆分 config、state、tools、graph、service；
- 引入 ruff、类型检查、pytest、pre-commit；
- 写第一篇 ADR，解释拆分边界；
- 为工具输入增加边界测试。

Definition of Done：lint/type/test 在一条命令内执行；至少 20 个测试；单文件不再承载全部职责。

## 第 4 周：HTTP 服务和月度复盘

主问题：Agent 能否作为可观测服务运行？

任务：

- 增加 HTTP API 和健康检查；
- 结构化错误和 request ID；
- 区分用户错误、依赖错误和内部错误；
- 做第一次月度记分；
- 发布工程化前后差异说明。

Definition of Done：API/健康检查有集成测试；每个请求有 request ID；完成月度复盘。

## 第 5 周：异步、流式和取消

主问题：客户端离开后，系统能否停止无意义工作？

任务：

- 学习 asyncio task、queue、timeout、cancellation；
- 实现 SSE 或等价流式响应；
- 客户端断开时取消模型/工具任务；
- 测试取消和超时；
- 记录资源释放证据。

Definition of Done：取消不会留下后台任务；超时路径有测试；流式响应可重复运行。

## 第 6 周：并发和背压

主问题：负载增长时系统如何退化？

任务：

- 加并发限制和队列；
- 运行 1/5/10/20 并发实验；
- 输出 P50/P95 和错误率；
- 注入慢工具和慢模型；
- 记录背压策略。

Definition of Done：有原始数据；能解释饱和点；过载时系统拒绝或排队而不是失控。

## 第 7 周：Tracing 和故障注入

主问题：失败时能否定位到具体组件？

任务：

- 为模型、工具、路由增加 span/结构化耗时；
- 注入超时、429、500、非法结构；
- 验证重试不会重复副作用；
- 建立第一个 failure taxonomy。

Definition of Done：五类失败都能从 trace 定位；至少一个问题由 tracing 发现并修复。

## 第 8 周：Gate 0A——工程地基审计

主问题：是否具备进入模型推理学习的工程基础？

任务：

- 30 分钟稳定运行；
- 干净环境复现；
- 汇总测试、类型、并发和故障结果；
- 写不少于 2,000 字复盘；
- 判断是否愿意继续系统调试。

Definition of Done：核心测试通过；复现文档有效；没有未解释的资源泄漏；明确继续或转向。

## 第 9 周：Tensor、dtype 和设备

主问题：数据类型和设备如何影响正确性、速度与内存？

任务：

- 完成 tensor、broadcast、linear、softmax 小实验；
- 对比 NumPy/PyTorch；
- 对比 FP32/FP16/BF16（硬件支持范围内）；
- 使用 profiler 和显存统计；
- 为数值容差写测试。

Definition of Done：至少 10 个小实验；每个实验有假设、结果和解释。

## 第 10 周：Attention

主问题：自回归 attention 的计算和数据流是什么？

任务：

- 手写单头 causal attention；
- 与 PyTorch 参考实现对比；
- 增加 mask、不同长度和 dtype 测试；
- 画 tensor shape 流程图。

Definition of Done：输出在定义容差内一致；能不看代码解释每个 tensor shape。

## 第 11 周：最小 Decoder 与 profiler

主问题：一次 token 生成的主要成本在哪里？

任务：

- 实现多头 attention、MLP、norm 和最小 Decoder block；
- 加逐 token 生成；
- profile 不同输入长度；
- 区分 host、算子和数据搬运开销。

Definition of Done：有运行测试、profile trace 和瓶颈假设；不以单次计时下结论。

## 第 12 周：KV Cache 与 Gate 0B

主问题：KV Cache 的收益、显存代价和适用边界是什么？

任务：

- 实现有/无 KV Cache 两条路径；
- 验证输出一致；
- 对不同上下文和输出长度重复至少 5 次；
- 报告时间、内存和变异；
- 写季度复盘与下一阶段决策。

Definition of Done：能用实验而非背诵解释 KV Cache；报告含原始数据、环境和限制；通过季度 Gate Review。

## 12 周结束时必须回答

1. 我是否真正喜欢性能测量和系统调试？
2. 我能否稳定投入每周至少 10–15 小时？
3. 我能否设计重复实验并区分噪声？
4. 我是否能独立解释异步取消、背压、attention 和 KV cache？
5. 我是否已经形成一个可维护项目，而不是更多 Demo？

前三个问题任意两个为“否”，暂缓购买硬件和深入 GPU Infra，先转向 Agent 评测/可靠性或补工程基础。
