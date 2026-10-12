# SGLang 源码与工作负载副线：Week 3–12

> 目标：Week 3–12 每周投入核心 2 小时（Week 12 为 2–3 小时）接触主项目。它不是额外课程；它把当周工程/模型练习连接到固定版本的真实源码、测试与一个可累积 workload 资产。

## 使用规则

1. 先执行 [源码工作流](SGLANG_SOURCE_WORKFLOW.md) 和 [SGLang 源码练习册](SGLANG_EXERCISE_MANIFEST.md)；每周的 `SGLANG_LAB.md` 是它们的主题导航，而非独立的随意阅读；
2. Week 3 首次 clone 后固定 `git rev-parse HEAD`；此后每张卡都写 SHA、文件/符号和日期，不能只写 `main`；
3. 每周提交阅读卡、一个 collect/run 命令或明确环境阻塞、一个调用图/状态图/契约，以及“能类比/不能类比”的表；
4. 不复制粘贴大型源码；只回答“输入是什么、输出/状态变化是什么、失败在哪里、测试如何证明”；
5. 看到未知概念时记录为问题，不在同一周扩展到内核/分布式源码；任何本地运行必须使用独立 venv、明确模型大小和显存预算，不污染 Agent 环境。

## 周次路线

| 周 | 主项目入口 | 只读/只做范围 | 当周产出 | 与主线的连接 |
| ---: | --- | --- | --- | --- |
| 3 | [contribution guide](https://docs.sglang.ai/developer_guide/contribution_guide.html) | test categories、repo setup、PR expectations | `sglang_notes/w03_contribution_map.md` | 质量门、类型、测试边界 |
| 4 | [server usage](https://docs.sglang.ai/basic_usage/openai_api_completions.html) | OpenAI-compatible request/response fields | `w04_api_contract_compare.md` | HTTP schema、错误、request ID |
| 5 | [session-aware radix cache](https://github.com/sgl-project/sglang/blob/main/docs/docs/advanced_features/session_radix_cache.mdx) | session_id、close_session、取消/错误路径的资源语义 | `w05_session_lifecycle.md` | stream/cancel/cleanup |
| 6 | [bench serving](https://docs.sglang.ai/developer_guide/bench_serving) | concurrency、request rate、JSONL details、TTFT/ITL | `w06_metric_mapping.md` | 背压、原始负载数据 |
| 7 | [`scheduler.py`](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/managers/scheduler.py) | 初始化/请求生命周期的 1 条调用线 | `w07_scheduler_trace.md` | trace、故障注入、request lifecycle |
| 8 | [open issues](https://github.com/sgl-project/sglang/issues) | 找 1 个 cache/scheduler/test 标签 issue，读模板/讨论 | `w08_issue_card.md` | Gate 后的贡献可行性 |
| 9 | [`model_runner` 相关目录](https://github.com/sgl-project/sglang/tree/main/python/sglang/srt/model_executor) | tensor/device/dtype 出现的位置，最多 3 个函数 | `w09_tensor_inventory.md` | Tensor/dtype/device |
| 10 | [`layers` 目录](https://github.com/sgl-project/sglang/tree/main/python/sglang/srt/layers) | attention backend 接口名和 tensor shape 问题 | `w10_attention_question.md` | causal attention reference |
| 11 | [`schedule_policy.py`](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/managers/schedule_policy.py) | prefill/decode、waiting queue、priority 的概念 | `w11_prefill_decode_map.md` | decoder timing / scheduler |
| 12 | [`radix_cache.py`](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/mem_cache/radix_cache.py) 与 session cache 文档 | match/insert/evict 的函数名与单请求 local cache 差异 | `w12_cache_gap_analysis.md` | local KV cache → prefix/session cache |

## 每周源码阅读卡模板

```markdown
# Week __ SGLang 阅读卡

来源 URL / commit：
阅读日期：
本周只回答的问题：

## 入口到出口

输入：
关键函数/对象（最多 5 个）：
状态变化：
输出/副作用：

## 与本周练习的映射

我的最小实现：
SGLang 同类概念：
相同点：
不能直接类比的地方：

## 一个可验证问题

问题：
需要什么测试/trace/benchmark 才能回答：
```

## 12 周后进入源码贡献的最低证据

- 有 10 张阅读卡，不是 10 个网页书签；
- 能从 server/API → scheduler → cache → model execution 画出高层调用图；
- 能说清 local KV cache、prefix cache、session-aware cache 的边界；
- 至少找到一个可复现、可测、范围小的 issue 或测试缺口；
- 能引用自己 Week 6、7、12 的数据说明为什么它值得研究。

这只是开始贡献的门票，不是“已经读懂全部 SGLang 源码”的虚假证明。
