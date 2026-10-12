# Week 10 练习册

## E1：shape 卡

对 `B=2,T=4,C=8,H=2,D=4` 写 X/Q/K/V/scores/weights/output 的 shape；每个 reshape/transpose 标原因。

## E2：手算 score

对 `B=H=1,T=3,D=2` 手算一条 QKᵀ score、缩放、mask、softmax、输出的至少一个元素，再与代码比较。

## E3：mask 反例

先故意使用上三角或漏 mask；写“改变未来 V 不应影响过去 output”的失败测试，再修复。

## E4：多头守恒

测试 H=1 与单头等价；C 不能整除 H 必须明确报错；H=2 时 merge 后 C 不变。

## E5：SDPA oracle

固定 Q/K/V、关闭 dropout，对 CPU FP32 与 SDPA 对照；出现差异时记录最大误差位置、mask、shape，而不直接改 atol。

## E6：复杂度预测

预测 T 从 16 到 64、256 时 score matrix 元素数变化；profile 后比较观察与理论，明确 reference 非 kernel benchmark。
