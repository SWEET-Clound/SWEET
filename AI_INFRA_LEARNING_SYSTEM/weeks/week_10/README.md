# Week 10：从零实现 causal attention 并验证

> 10 小时核心 + 5 小时深化。主问题：**自回归 attention 的每个 tensor 在做什么，causal mask 为什么保证不看未来？**

执行顺序：[START_HERE.md](START_HERE.md) → 本 README 的五个板块 → [RESOURCES.md](RESOURCES.md) 指定段落 → [EXERCISES.md](EXERCISES.md) → [WORK_LOG.md](WORK_LOG.md) → [RUBRIC.md](RUBRIC.md)。

## 正确性优先模型

```text
X [B,T,C]
 → Q,K,V [B,H,T,D]
 → scores = QKᵀ / √D [B,H,T,T]
 → causal mask + softmax [B,H,T,T]
 → weights V [B,H,T,D]
 → output [B,T,C]
```

## 五个学习板块

### W10-1：shape 手算与缩放原因（2.5 小时）

1. 固定 `B=2,T=4,C=8,H=2,D=4`，填完整 shape 表；
2. 用 `B=1,H=1,T=3,D=2` 手算一个 QK score；
3. 比较有/无 `1/sqrt(D)` 时 softmax 最大概率，说明缩放抑制 logits 随维度变大而过饱和；
4. 写 assert：`C % H == 0`，错误 shape 必须清晰失败；
5. 画矩阵中第 i 行能/不能看哪些 token。

**产出：** `shape_flow.md` 和一页手算证据。

### W10-2：单头 reference 实现（3 小时）

**路径：**

1. 实现只接受 `[B,T,D]` 的 `causal_attention_reference(q,k,v)`；
2. 用显式下三角 bool mask，把未来 score 设为 `-inf`；
3. softmax 最后一维；
4. 测 T=1、不同 B、相同 token、极端 score；
5. 测未来 V 改变不会影响早期 output，过去 V 改变会影响允许位置。

**练习：** 先故意用上三角 mask，写会失败的“未来不影响过去”测试，再修正。

### W10-3：多头 reshape 与合并（3 小时）

**知识：** 多头不是多个 batch；它将 channel 切成 H 份后各自计算 attention，再 transpose/reshape 回 C。`view` 对 contiguous 有要求，`transpose` 改变 stride。

**路径：** 写 projection、split heads、attention、merge heads；在每一步 assert shape 与 contiguous 预期；用固定权重使结果可比较。

**测试：** H=1 等价单头；H>1 shape；C 不能整除 H；B/T 变化；mask 不泄漏未来。

### W10-4：同 PyTorch SDPA 对照（3.5 小时）

1. 对齐 Q/K/V layout 与 `scaled_dot_product_attention`；
2. 关闭 dropout；明确 `is_causal=True` 与 bool mask 语义；
3. 在 FP32 CPU 以严格容差比较；GPU FP16/BF16 以记录过的放宽容差比较；
4. 若不一致，打印最大误差位置、输入 shape、mask，而非随意放宽 atol；
5. 增加不同 length、dtype、batch 的参数化测试。

**资源：** [PyTorch SDPA API](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention)。注意它的 bool mask 语义与 `MultiheadAttention` 不同。

### W10-5：解释与性能边界（3 小时）

1. profile T=16/64/256 的 reference attention；
2. 报告 score matrix 为什么是 O(T²)；
3. 不与 FlashAttention 直接宣称公平性能比较；reference 目标是正确性和可解释性；
4. 写 `attention_correctness_report.md`：覆盖、容差、局限、未覆盖 kernel；
5. 口试：Q/K/V、mask、softmax 维度、head reshape、为什么未来不可见。

**本周 DoD：** reference 与 PyTorch 对齐；mask/shape/dtype 测试齐全；不看代码能解释每个 tensor shape。
