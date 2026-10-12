# Week 9 SGLang Lab：model executor 的 tensor/dtype/device 清单

**时间：核心 2h。**

1. 用固定 SHA 定位 `python/sglang/srt/model_executor`（以实际路径为准），只选最多三个函数/类。
2. 写 `sglang_notes/w09_tensor_inventory.md`：符号、输入/输出 shape（未知写 unknown）、device/dtype 线索、是否实际执行过、对应本地实验 ID。
3. `rg -n "dtype|device|torch\." <directory>` 获取线索；不能由字符串命中推断运行语义。
4. 选择一个 CPU-safe test collect/run，记录环境与结果。

**验收**：SHA、最多三项 inventory、真实符号、测试证据、至少两个“无法从本地 tensor lab 类比”的点（例如并行、量化、runtime memory manager）。
