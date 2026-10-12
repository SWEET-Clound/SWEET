# Week 12 练习册

## E1：缓存字节推导

对 `B,H,T,D,L,dtype` 推导 `2*B*H*T*D*L*bytes`；T 翻倍的理论变化是什么？与实际 tensor nbytes 对照。

## E2：cache 不变量表

填写 cache layer 数、K/V shape、dtype、device、sequence length、会话归属。对每项写一个应失败的错误例。

## E3：输出等价

对 T=1、短 prompt、不同 batch、EOS、最大长度比较 cache/no-cache logits 和 token 序列。任何差异先在 CPU FP32 单层最小化。

## E4：concat 维度反例

故意在错误维度 append K/V；先写会失败的 cache length 或输出一致性测试，再修复。

## E5：benchmark 预注册

在运行前填 context `16/64/256/512` × output `1/16/64`、5 repeats、控制变量、指标、OOM 处理。不得事后删不利数据点。

## E6：反例与映射

找一项收益不明显/受限结果，提出至少两个假设。写 local cache 与 prefix/session/Paged cache 在 sharing、allocation、eviction、admission、isolation 上的差别。
