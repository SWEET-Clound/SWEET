# Week 12 资料索引

| 优先级 | 资料 | 只读范围 | 必须产出 |
| --- | --- | --- | --- |
| 必读 | [SGLang docs](https://docs.sglang.ai/) | KV cache/serving 入口 | local vs serving cache 表 |
| 必读 | [vLLM docs](https://docs.vllm.ai/) | PagedAttention/KV cache 入口 | Week 13 对照问题 |
| 必读 | [PyTorch SDPA](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention) | Q/K/V shape | cache shape 约束 |
| 原论文 | [vLLM/PagedAttention](https://arxiv.org/abs/2309.06180) | abstract/introduction | 为什么 page/block 问题超出本周 |
| 原则参考 | [SGLang agentic cache roadmap](https://github.com/sgl-project/sglang/issues/27574) | session/prefix 问题清单 | 后续研究问题 |

不读：完整 SGLang scheduler 源码、分布式 KV transfer、量化/稀疏 cache 优化。
