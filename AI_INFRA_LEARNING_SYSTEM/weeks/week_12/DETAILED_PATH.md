# Week 12 逐次执行手册：KV Cache 正确性、基准与 Gate 0B

> 核心 10h + 深化 5h。目标是首先证明 cache/no-cache 一致，再用重复原始数据讨论代价；不是复刻 RadixAttention/PagedAttention。

## 精确 Cache contract

```text
Cache = tuple[LayerCache, ...]
每层 K/V: [B,H,T_cached,D]，同 device、同 dtype；无全局 cache。
prefill [B,Tp] → logits [B,Tp,V] + cache len Tp。
decode [B,1] + cache len Tc → logits [B,1,V] + cache len Tc+1。
K/V append 维度为 -2；prefill positions 0..Tp-1，decode position Tc。
新请求传 None；cache 只能由同一请求/模型/config 使用。
```

本周 decode 的 query 长度为 1、key length 为 `Tc+1`。必须构造显式正确 mask/position 语义；**不能**把方形 `is_causal=True` 的默认行为外推到非方形 Q/KV。

## Session 1（2h）：先让不变量成为红测试

在 `labs/decoder_lab/tests/test_cache_contract.py` 写：prefill cache shape、decode 增量长度、层数/B/H/D/device/dtype、拒绝不匹配 cache。再写“错误 concat 维度”的红色例子，确保测试真能捕捉它。不要在测试中只检查 cache 非空。

## Session 2（2h）：缓存实现与 logits 对照

实现 layer-local `LayerCache` 和 append。CPU FP32 下逐 token 对比：对同一 prompt，no-cache 生成的每一步 logits 与 `prefill + decode` 每一步 logits `assert_close`；先小 config B=1/H=1/T短，再扩大。出现不同先排查 position、mask、concat dim、eval/dropout、权重共享，禁止先放宽容差。

## Session 3（2h）：边界测试矩阵

至少覆盖：T=1、短/长 prompt、B=2 isolation、多个 layer、EOS、max context boundary、new request uses None cache、错误 dtype/device/config、cache/no-cache token equality。GPU/低精度测试可 skip，但 CPU FP32 不可跳过。每个 case 写明确测试名与失败说明。

## Session 4（2h）：公平 benchmark

矩阵：context `16/64/256/512` × generate `1/16/64` × repeats≥5（资源不足缩小且注明）。同权重、prompt、seed、device、dtype、warmup。CSV：

```text
revision,model_config,device,dtype,mode,context_len,output_len,repeat_index,prefill_ms,first_decode_ms,decode_total_ms,tpot_ms,total_ms,peak_memory_bytes,output_equal,oom,error
```

无 cache 与 cache 分开记录；OOM 留行、不能删除。TPOT 用 first token 后 steps 的平均，`output_len<2` 标 `NA`。

## Session 5（2h）：报告、映射与 Gate 0B

报告固定标题：环境与 revision；contract；correctness matrix；方法；raw data；图（TPOT/context、memory/context）；结果；一个收益有限/反例；局限；Week13 问题。再写表：

| 本地概念 | SGLang/vLLM 入口 | 本周证明 | 未证明 | 下周问题 |
| --- | --- | --- | --- |

完成 [SGLang Lab](SGLANG_LAB.md)。Gate 0B 仅在 logits/token 一致、每点 raw repeats、报告和局限完整时通过。

## 深化与真实边界

可以做 cache memory 理论/实测对照或固定 context 下 profile；不研究 page allocation、跨请求 prefix sharing、eviction、P/D disaggregation。完成需 10+ correctness tests、raw CSV、两图、映射表、Gate 0B。
