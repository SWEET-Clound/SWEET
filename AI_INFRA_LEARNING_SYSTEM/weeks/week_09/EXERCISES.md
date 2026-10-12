# Week 9 练习册

## E1：shape 预测

不运行代码，填写 `X[B,T,C] @ W[C,V]`、QKV split、softmax 的 6 个输入输出 shape；运行后对照。

## E2：broadcast 真/假

设计 4 个会 broadcast、2 个必须报错的例子；解释每一维为何匹配/不匹配。

## E3：stable softmax

自己实现减 max 的 softmax；测试行和为 1、平移不变、极端 logits 无 NaN，并与 torch 对照。

## E4：dtype 误差表

对 FP32/FP16/BF16（支持时）同一 matmul 记录 max abs、relative error、allclose 参数、时间、理论字节数。

## E5：device 错误

故意混合 CPU/CUDA 非标量 tensor，预测/记录报错；明确修复是显式移动，而不是接受隐式行为。

## E6：可信计时

为 matmul 写 warmup/repeats/synchronize 计划；保存每次时间、median/P95，不把第一次或 print 计入。
