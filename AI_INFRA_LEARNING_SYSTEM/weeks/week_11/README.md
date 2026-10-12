# Week 11：最小 Decoder、逐 token 生成与 profiler

> 10 小时核心 + 5 小时深化。主问题：**一次 token 生成经过哪些模块，主要成本在哪，如何证明而不是猜测？**

从 [START_HERE.md](START_HERE.md) 开始；用 [RESOURCES.md](RESOURCES.md) 控制阅读范围；实现前完成 [EXERCISES.md](EXERCISES.md) 的 shape/生成预测；记录到 [WORK_LOG.md](WORK_LOG.md)，按 [RUBRIC.md](RUBRIC.md) 验收。

## 五个学习板块

### W11-1：Decoder block 的契约（2.5 小时）

定义最小 pre-norm block：token embedding + positional information → norm → causal MHA → residual → norm → MLP → residual → logits。每层写输入/输出 shape、参数 shape、是否改变 sequence length。

**练习：** 用 `B=2,T=5,C=16,H=4,V=32` 填全参数和 activation shape；计算 embedding、QKV、MLP 的参数量并用 `sum(p.numel())` 对照。

### W11-2：实现与局部正确性（3.5 小时）

1. 先写 LayerNorm、MLP、block 的最小测试；
2. 保证 eval mode dropout 为 0；
3. 测 logits shape、固定 seed 可重复、输入 token 越界报错、T 超 context 报错；
4. 测 residual 不是原地破坏输入；
5. 保持模型小，绝不下载大 checkpoint 逃避 shape 理解。

**产出：** `labs/decoder_lab/` 与 12+ unit tests。

### W11-3：自回归 generate 与停止语义（3 小时）

**知识：** prefill 处理已有 prompt；decode 每轮输入一个新 token、产生下一个 token。采样不是 argmax 的同义词。

**路径：**

1. 先实现 deterministic greedy `generate(prompt,max_new_tokens)`；
2. 测输出长度、EOS 早停、max_new_tokens=0、prompt 为空/过长；
3. 每轮记录 token index、输入长度、耗时；
4. 写 trace 表分开 prefill 与每步 decode；
5. 不把随机采样加入正确性 benchmark；若加，固定 generator 并单独标注。

### W11-4：测量与 profiler（3.5 小时）

**路径：**

1. 固定模型、dtype、device、seed、warmup；
2. 测 prompt T=16/64/128 与输出 1/16/64，重复 5 次；
3. 分别报 prefill time、first-token latency、每 token latency、总 time、峰值内存；
4. 用 `torch.profiler` 捕捉 prefill 和一个 decode step，列 host、算子、GPU kernel/数据搬运的可见证据；
5. 写瓶颈假设，不因一个 top operator 就声称已找根因。

### W11-5：为 KV Cache 做正确性基线（2.5 小时）

保存 no-cache 每次完整重算的 generate 输出、计时、内存。写下假设：cache 应避免重复计算历史 K/V，使 decode 的每步输入从 T 变为 1，但 cache 自身占用随长度增长。

**本周 DoD：** 最小 decoder 可 generate；有 prefill/decode 分离数据和 profiler trace；正确性/性能基线可复跑。总测试建议 80+。
