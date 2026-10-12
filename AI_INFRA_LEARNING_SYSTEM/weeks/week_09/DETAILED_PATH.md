# Week 9 逐次执行手册：Tensor、dtype、device 与测量

> 核心 10h + 深化 5h。建立 `labs/tensor_lab`，所有 GPU 内容都有 CPU 正确性后备路径。

## 进入 Gate 与实验脚手架

必须有 Week 8 “继续”或明确的回补计划。新建：

```text
labs/tensor_lab/
  src/{shapes.py,stable_softmax.py,benchmark.py,dtype_report.py}
  tests/{test_shapes.py,test_softmax.py,test_memory.py,test_device.py}
  experiments/{raw,profiler}/  environment.json  results.csv  report.md
```

先运行：`.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.version.cuda)"`。若 PyTorch/Python 3.14 组合无官方 wheel，记录版本冲突，使用兼容的独立实验环境；不从不明来源下载 wheel。Windows/WSL 任选一个环境为主，不能混用 CUDA 驱动结论。

## 10 个微实验（每个：预测→测试→raw→解释）

| ID | 内容 | 最低断言 |
| --- | --- | --- |
| 01 | `[B,T,C] @ [C,V]` linear | 输出 `[B,T,V]` |
| 02 | 合法 broadcast | 与显式扩展结果相同 |
| 03 | 非法 broadcast | `pytest.raises(RuntimeError)` |
| 04 | stable softmax | sum≈1、平移不变、无 NaN |
| 05 | FP32 reference | 输出保存为比较基准 |
| 06 | FP16/BF16 | 记录误差或 capability skip |
| 07 | 理论内存 | `numel*element_size == nbytes` |
| 08 | device 边界 | CPU/CUDA 混用明确失败或 skip |
| 09 | timer | warmup/repeats 原始单次结果 |
| 10 | profiler | 至少一个 trace 或明确 CPU-only 限制 |

## Session 1（2h）：shape、linear、broadcast

实现实验 01–03 和 `test_shapes.py`。每个测试必须写具体 `[B,T,C]` 值；不能只打印 shape。非法例应 `raises`，不将异常吞掉。

## Session 2（2h）：数值稳定 softmax

实现 `stable_softmax(x, dim=-1)`：先减最大值。测试 `[1000,1001]`、全零、平移、行和。与 `torch.softmax` 仅在同 dtype/device 下比较。保存误差定义：`max_abs = max(|a-b|)`；相对误差需说明接近零处理。

## Session 3（2h）：dtype、device、内存

FP32 是 CPU primary oracle。低精度/ GPU 使用 `torch.testing.assert_close` 的明确 atol/rtol，记录为何不同。CUDA 测试用 `@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")`。禁止无意 `.cpu()`/`.cuda()` 修正错误；每次迁移必须显式。

## Session 4（2h）：可复现计时和 profiler

`benchmark(fn, warmup=10, repeats=30)`：CPU 用 `perf_counter_ns`；CUDA 在测量边界 `torch.cuda.synchronize()`，可用 event 但要说明方法。数据创建/打印/首次编译不计入区间。CSV 必须有：

```text
experiment_id,revision,device,dtype,shape,warmup,repeats,run_index,elapsed_ms,peak_memory_bytes,max_abs_error,max_rel_error,notes
```

CUDA 时 reset/read peak memory；CPU 不伪造显存。运行 profiler，导出 trace；它给出 operator 时间线，不单独证明全系统瓶颈。

## Session 5（2h）：报告与资产

完成实验 01–10，报告按“环境、假设、方法、raw、结果、可解释、不能推断”写。更新 workload 的 token/shape/cost 注释。执行 [SGLang Lab](SGLANG_LAB.md)。

## 深化与完成定义

做一张 dtype/device capability table。完成：10 微实验、10+ 强断言测试、环境 JSON、CSV、trace/限制、报告。没有 CUDA 仍可以通过正确性路径，但 GPU 性能项必须标 `UNVERIFIED`。
