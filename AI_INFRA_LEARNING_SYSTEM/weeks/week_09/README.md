# Week 9：Tensor、dtype、设备与数值实验

> 10 小时核心 + 5 小时深化。主问题：**数据形状、dtype、设备怎样共同决定推理的正确性、内存和速度？**

从 [START_HERE.md](START_HERE.md) 创建实验本；按 [RESOURCES.md](RESOURCES.md) 定向阅读；在实现前完成 [EXERCISES.md](EXERCISES.md) 的预测；所有命令、版本、数据和解释写入 [WORK_LOG.md](WORK_LOG.md)，周末用 [RUBRIC.md](RUBRIC.md) 验收。

这周不训练大模型。你用小而可验证的 Tensor 实验建立之后解释 attention、KV cache 和 GPU profiling 必需的语言。

## 五个学习板块

### W9-1：可复现实验环境（2 小时）

**路径：**

1. 在 WSL 或当前 Windows venv 中安装与 CUDA/驱动匹配、实际可 import 的 PyTorch；记录完整版本、`torch.version.cuda`、`torch.cuda.is_available()`、GPU 名称；
2. 建立 `labs/tensor_lab/`：`src/`、`tests/`、`experiments/`、`README.md`；
3. 固定随机种子并记录不能保证所有 CUDA 算法确定性的限制；
4. 每个实验都写假设、环境、命令、原始数据、解释；
5. 若 CUDA 不可用，先在 CPU 完成正确性实验，把 GPU 项标为硬件阻塞，不用猜测替代数据。

**产出：** `environment.json` 与可复制的安装/验证命令。

### W9-2：shape、broadcast、linear、softmax（3.5 小时）

**知识：** tensor 的 shape 决定每个维度语义；broadcast 是按照规则扩展 size=1 维，不是自动“把任何东西凑起来”；linear 是矩阵乘法加 bias；softmax 沿指定维归一化。

**路径：**

1. 手写 shape 表：`[batch, sequence, hidden] @ [hidden, vocab] → [batch, sequence, vocab]`；
2. 用 2×3、3×4 小矩阵手算一个元素，再与 torch 比较；
3. 做 4 个 broadcast 正例和 2 个应失败例；
4. 实现 stable softmax（减去 max），与 `torch.softmax` 比较；
5. 写测试：每行概率和约等于 1、平移 logits 不变、极端 logits 不出 NaN。

**练习：** 在不运行代码前，写出 6 个操作的输入/输出 shape；运行后比对。

**产出 / 验收：** 4 个小实验、10+ shape/numerical tests、shape_flow.md。

### W9-3：dtype、设备与误差预算（3.5 小时）

**知识：** dtype 是数值表示；device 是数据所在设备，非标量 tensor 不会自动在 CPU/GPU 间移动。FP16/BF16/FP32 的比较必须同时报告误差、速度、内存和支持条件。

**路径：**

1. 对相同 matmul 在 FP32、FP16、BF16（支持时）运行，FP32 做参考；
2. 记录 max absolute error、relative error、`allclose` 的 rtol/atol；
3. 故意构造 CPU×CUDA 的错误并解释报错；不要通过隐式复制“修复”；
4. 计算理论存储：`numel * element_size`，与 `tensor.nbytes` 对照；
5. 对每种 dtype 写“何时可用、何时不应信任”的限制。

**资源：** [Tensor dtype/device 属性](https://docs.pytorch.org/docs/stable/tensor_attributes)；[AMP 示例](https://docs.pytorch.org/docs/stable/notes/amp_examples.html)。

**练习：** 预测长度翻倍时一个 `[B,H,T,D]` FP16 K cache 的理论字节数如何变化；Week 12 再验证。

### W9-4：正确计时、显存与 profiler（3.5 小时）

**路径：**

1. 编写 `benchmark(fn, warmup, repeats)`；CUDA 情况下在测量边界同步；
2. 禁止把首次运行、下载、打印、数据创建混入被测区间；
3. 记录 median、P95、每次时间，不只记录最小值；
4. CUDA 时记录 `max_memory_allocated`，并在实验前 reset peak；
5. 对至少一个 matmul 用 `torch.profiler`，列前 5 个 operator，写“观察”与“因果假设”两列。

**资源：** [PyTorch Profiler](https://docs.pytorch.org/docs/stable/profiler)。

**产出：** 10 个微实验中的前 8 个，raw CSV 和 profiler trace。

### W9-5：实验报告与 Gate（2.5 小时）

完成 10 个小实验（shape、broadcast、linear、softmax、dtype、device、误差、内存、计时、profiler），每个具备假设/命令/结果/解释。口试：dtype 与 device、allclose 参数、为何需要 warmup、profile 只能说明什么。

**本周 DoD：** 10 个实验、10+ 测试、原始数据、至少一个 trace。不能从单次 GPU 计时推出“更快”。
