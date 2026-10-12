# Week 10 逐次执行手册：causal attention 的正确性与复杂度

> 核心 10h + 深化 5h。只实现 full-prefill、`Tq == Tk` 的 reference attention；cache decode、位置偏移留给 Week 12。

## 稳定接口与语义

```python
def causal_attention_reference(q, k, v, *, causal: bool = True):
    # q/k/v: [B, H, T, D], only Tq == Tk this week
    # -> [B, H, T, D]
```

`Tq != Tk` 本周 `ValueError`，而非假装支持。mask 语义：第 i 个 query 只能看 key `0..i`；mask 后 logit 在 softmax 前为 `-inf` 或等价安全实现。不要把非方形 SDPA 的 `is_causal` 行为用于 decode 推断。

## 脚手架

```text
labs/attention_lab/src/{reference_attention.py,multihead.py}
labs/attention_lab/tests/{test_reference_attention.py,test_causal_property.py,test_multihead.py,test_sdpa_oracle.py}
labs/attention_lab/docs/shape_flow.md
labs/attention_lab/experiments/{complexity.csv,attention_correctness_report.md}
```

## Session 1（2h）：手算与 shape contract

用固定小 Q/K/V（B=H=1,T=2,D=1）手算 scaled score、mask、softmax、输出，把答案写进测试注释。手算通过前不要调用 SDPA。为 T=1、shape 不同、`Tq!=Tk` 写红测。

## Session 2（2h）：reference 实现与因果性质

实现 `scores = q @ k.transpose(-2,-1) / sqrt(D)`，再明确 mask、softmax、`@v`。测试：改变未来 key/value 不影响 earlier output；改变当前/过去 value 会影响可见 output；无 mask 版本与 causal 版本不同。任何 `nan` 先检查全 masked 行和 mask dtype。

## Session 3（2h）：多头包装与 oracle

实现 H-head split/merge，要求 `C % H == 0`，不满足抛 ValueError。验证 H=1 等价性和 B/T/H 参数化。CPU FP32 对 PyTorch SDPA 用严格容差；GPU/低精度是近似测试并 skip 守卫。记录 SDPA API/版本，不能将其当绝对真理。

## Session 4（2h）：成本与实验

以 T=`16,32,64,128`（按硬件缩小）测 reference 前向。记录 B,H,D、device、dtype、warmup/repeats、单次 raw timing。将理论 score matrix O(T²) 与实测趋势并列，但不从四个点推断生产 engine 性能。

## Session 5（2h）：失败目录和报告

写 `docs/failure_catalog.md`：mask 转置、`view` 非 contiguous、scale 漏除、softmax 维错、SDPA mask 语义、dtype tolerance。报告须显示 handcalc、性质测试、oracle 结果、复杂度表和局限。完成 [SGLang Lab](SGLANG_LAB.md)。

## 深化

可增加 dropout=0 的 explicit test，或对比 naive/reference；不进入 flash attention/kernels。完成需测试矩阵、手算、因果反例、SDPA 对照与 CSV。
