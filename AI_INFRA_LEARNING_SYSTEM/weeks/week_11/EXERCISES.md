# Week 11 练习册

## E1：Decoder 参数/shape 表

对 `B=2,T=5,C=16,H=4,V=32` 写 embedding、QKV、MLP、logits 的 shape及参数量；与 `sum(p.numel())` 对照。

## E2：局部模块契约

为 LayerNorm、MLP、residual 各写一个不变量：shape、非原地修改、eval 可重复。先写测试再实现。

## E3：greedy generate

测试：max_new_tokens=0、EOS 早停、prompt 过长、token 越界、输出长度。记录每步输入 length 和产生 token。

## E4：prefill / decode 划分

给 prompt 4、输出 3 的生成画时间表：哪一步是 prefill，哪三步是 decode？每步输入多少 token？

## E5：公平测量

为 T=16/64/128 × output=1/16/64 写重复 5 次 matrix；保存 raw timing。先预测哪项随 prompt 增长更明显。

## E6：profiler 解释

从 prefill 和一个 decode trace 各选 top 3 operator，写“观察 / 不足以证明 / 下一步假设”。
