# Week 12 从这里开始

KV Cache 是本路线第一次直接触及 SGLang/vLLM 核心问题。但本周实现的是单请求、单机、最小 Decoder 的 local KV cache；它不是 RadixAttention、PagedAttention 或多租户 session cache。

进入 Gate：Week 11 no-cache baseline、固定权重/seed、prefill/decode trace 都存在。先证明 cache/no-cache 输出一致，再做任何 benchmark。

结束时必须能用原始数据回答：省了什么计算、增加了什么显存、哪些场景收益不明显、这同 serving cache 有何不同。
