# Week 10 从这里开始

本周要亲手实现的不是“一个能生成文本的模型”，而是一个可检查的 causal attention reference。reference 的首要标准是正确、shape 清楚、测试充分；不是比 PyTorch kernel 快。

进入 Gate：Week 9 形状、dtype、device、allclose 实验已完成。每次不一致先缩小到 `B=H=1,T=3,D=2,FP32,CPU`，再排查；禁止先放宽容差。

最终你要能在纸上画出 `[B,T,C] → [B,H,T,D] → [B,H,T,T] → [B,T,C]`。
