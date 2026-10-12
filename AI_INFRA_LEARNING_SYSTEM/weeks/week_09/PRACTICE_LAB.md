# Week 9 练习实验室

所有 torch 练习先 `torch.manual_seed(7)`；CPU FP32 是正确性 oracle。CUDA 项只有可用时运行，否则标记 skip/UNVERIFIED。

## P1：环境能力表（B1）

- 起始：`labs/tensor_lab/experiments/environment.json`。
- 命令：`.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.version.cuda)"`。
- 预期：记录 Python/torch/device/CUDA；无 CUDA 不失败，只转 CPU 路径。
- 反例：从 Windows 环境推断 WSL GPU 可用；两者必须分开记录。

## P2：固定 shape/broadcast/softmax（B2）

- 输入：`X=torch.ones(2,3,4)`、`W=torch.ones(4,5)`；合法 `[2,3,1]+[1,3,4]`；非法 `[2,3]+[4]`；logits `[1000.,1001.]`。
- 预期：linear shape `[2,3,5]`；非法 raise；stable softmax finite、sum=1、与 torch close。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/tensor_lab/tests/test_shapes.py labs/tensor_lab/tests/test_softmax.py`。
- 反例：不减 max 的 softmax 在极值失败。

## P3：dtype/device/error ledger（B3）

- 输入：同一固定 matmul 的 FP32、支持时 FP16/BF16；理论 `numel*element_size`。
- 预期：CSV 有 max_abs/max_rel/tolerance/nbytes；CPU×CUDA 操作明确错误/skip；显式迁移后才通过。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/tensor_lab/tests/test_memory.py labs/tensor_lab/tests/test_device.py`。
- 反例：低精度与 FP32 bitwise equal；不应作为断言。

## P4：30 次原始测量（B4）

- 输入：固定 shape、warmup=10、repeats=30。
- 预期：CSV 恰有30个 run_index；median/P95 从 raw 重算；CUDA 需同步，CPU 用 perf_counter。
- 反例：只存最小时间或把 tensor 创建放计时区；报告应拒绝。

## P5：profile 限定性解释（B5）

- 输入：P4 同一函数。
- 预期：有 trace 文件，或 `CPU_UNVERIFIED_GPU_PROFILE.md`；写 top operator、观察、不能证明、下一假设各一句。
- 验证：报告能指向 raw CSV/trace，不能声称发现生产模型瓶颈。
