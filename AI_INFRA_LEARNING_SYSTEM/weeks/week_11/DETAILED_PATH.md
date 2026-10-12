# Week 11 逐次执行手册：最小 Decoder、生成与 profiler

> 核心 10h + 深化 5h。实现一个小且确定的 no-cache decoder，提前留出 cache seam；本周不将本地计算偷换为网络 TTFT。

## 固定配置、接口与术语

默认配置（可因 CPU 缩小，但任何变化都写入报告）：

```text
vocab_size=64, max_seq_len=128, n_layers=2, n_heads=4,
d_model=32, mlp_ratio=4, dropout_p=0.0,
tie_embeddings=False, positional_embedding=learned,
bos_token_id=0, eos_token_id=1
```

稳定接口：

```python
forward(input_ids: LongTensor[B,T], *, past_key_values=None, use_cache=False) -> DecoderOutput
generate(prompt_ids: LongTensor[B,T], *, max_new_tokens: int, eos_token_id: int | None) -> LongTensor[B,T+N]
```

本周 `past_key_values` 必须为 `None`/不启用；`use_cache=True` 可明确拒绝或预留固定返回结构，禁止伪实现。术语：**prefill**=整段 prompt forward；**first-token compute**=取 prompt 末 logits 的计算；**TPOT**=第一个 token 后每步 local forward；`TTFT` 仅在后续 server 的端到端测量使用。

## 脚手架与 Gate

```text
labs/decoder_lab/src/{config.py,model.py,generate.py,benchmark.py}
labs/decoder_lab/tests/{test_config.py,test_forward.py,test_generate.py,test_determinism.py}
labs/decoder_lab/experiments/{raw,profiler}/ report.md
```

进入前 Week10 full-prefill attention tests 必须绿。CPU FP32 和 `model.eval()` 是 primary reference；dropout 必为 0。

## Session 1（2h）：config 与 shape flow

写 `DecoderConfig` 验证：`d_model % n_heads == 0`、max seq 正、vocab 正。为 input `[B,T]` → logits `[B,T,V]` 写测试。画 token id→embedding+position→blocks→norm→logits 的 shape 图。输入非 LongTensor、空 prompt、超过 context 的行为必须明确 `ValueError` 或测试的已定义 error。

## Session 2（2h）：单 block 的确定性 forward

将 Week10 attention 引入 block，加 MLP、residual、norm。先小 config，测试 finite logits、shape、batch isolation（改 batch0 不影响 batch1）、repeat forward equality。不要训练，也不要用随机 output 解释正确性。

## Session 3（2h）：greedy generate contract

`generate` 每步对最后 logits `argmax`，EOS 停止。测试：`max_new_tokens=0` 不变、立即 EOS、恰好 N、prompt 不被原地修改、相同 seed/config 输出相同。空 prompt 明确拒绝。保存一个固定 prompt 的 token-id golden result（仅用于版本内回归，不将随机权重跨平台当恒定真理）。

## Session 4（2h）：本地计时与 profiler

同模型、同 prompt、eval/no_grad 下分别测 prefill、first-token compute、后续 decode forwards、total。CPU fallback 使用小序列和原始 repeats；GPU 参照 Week9 同步规则。导出 trace，写 operator 观察和**假设**，不声称已找出生产瓶颈。

## Session 5（2h）：replay harness 与报告

建立 `workloads/agent_cache_lab/decoder_replay.jsonl`：seed、config hash、prompt token IDs、max new tokens、expected generated IDs、环境；不可放真实 prompt。报告必须列：模型 config/revision、设备/dtype、计时定义、raw 路径、determinism/shape tests、局限。完成 [SGLang Lab](SGLANG_LAB.md)。

## 深化与完成定义

做 weight tying 的一个对照或参数量计算，但不得进入 cache。完成：固定 config、no-cache forward/generate、determinism/edge tests、local timing/profiler、replay harness。
