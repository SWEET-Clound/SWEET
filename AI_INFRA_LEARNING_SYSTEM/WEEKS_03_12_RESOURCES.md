# Week 3–12 官方学习资料索引

阅读原则：每项资料只读本周指定范围；读完必须写“概念—当前代码/实验位置—最小验证—未解问题”四行。资料不是完成标准，练习与证据才是。

| 周 | 资料 | 只读范围 | 阅读后必须做到 |
| ---: | --- | --- | --- |
| 3 | [mypy getting started](https://mypy.readthedocs.io/en/stable/getting_started.html) | annotations、incremental adoption | 为路由/state 写类型并解释静态检查 |
| 3 | [Ruff tutorial](https://github.com/astral-sh/ruff/blob/main/docs/tutorial.md) | check、format、config | 让 lint/format 成为质量门 |
| 4 | [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/) | TestClient | 写不启 socket 的 API contract test |
| 5 | [asyncio tasks](https://docs.python.org/3/library/asyncio-task.html) | Task.cancel、timeout、finally | 证明 timeout/cancel cleanup |
| 6 | [statistics](https://docs.python.org/3/library/statistics.html) | median、quantiles | 对 raw latency 做可解释汇总 |
| 7 | [OpenTelemetry instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/) | span、attribute、exception | 设计且测试 trace contract |
| 9 | [Tensor attributes](https://docs.pytorch.org/docs/stable/tensor_attributes) | dtype、device | 解释 device/dtype 不匹配 |
| 9/11 | [PyTorch profiler](https://docs.pytorch.org/docs/stable/profiler) | context manager、schedule | 留一份 trace 与瓶颈假设 |
| 10 | [scaled dot-product attention](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention) | shapes、mask、causal | 与 reference attention 对照 |
| 12 | [SGLang docs](https://docs.sglang.ai/) | serving/KV cache 相关入口 | 写 local cache 与 serving cache 的差异 |
| 12 | [vLLM docs](https://docs.vllm.ai/) | KV cache/serving 概念入口 | 列 Week 13 的双后端问题 |

每周最多 20–45 分钟定向阅读；其余时间写代码、运行实验、解释结果。遇到新术语先在本周 `README.md` 的知识段落定位，不把它扩展成无边界搜索。
