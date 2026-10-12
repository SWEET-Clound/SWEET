# Week 10 资料索引

| 优先级 | 资料 | 只读范围 | 必须产出 |
| --- | --- | --- | --- |
| 必读 | [PyTorch SDPA](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention) | shapes、is_causal、mask | 对照测试 |
| 必读 | [MultiheadAttention](https://docs.pytorch.org/docs/stable/generated/torch.nn.MultiheadAttention.html) | mask 语义差异 | mask 注意事项 |
| 必读 | [PyTorch numerical accuracy](https://docs.pytorch.org/docs/stable/notes/numerical_accuracy.html) | allclose/后端差异 | 容差理由 |
| 原论文 | [Attention Is All You Need](https://arxiv.org/abs/1706.03762) | scaled dot-product 图和 multi-head 小节 | QKV/缩放解释 |

只读与实现有关部分；不读完整 Transformer 训练、位置编码变体、FlashAttention 论文。
