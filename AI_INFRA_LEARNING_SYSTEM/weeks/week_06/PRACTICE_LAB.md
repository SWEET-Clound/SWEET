# Week 6 练习实验室

## P1：并发上限（B1）

- 起始：`AdmissionGate(max_in_flight)` 与 `tests/test_admission.py`。
- 输入：N=2、四个由 barrier 同时释放的 fake request。
- 预期：记录 `active_peak <= 2`；全部任务最终 terminal。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_admission.py`。
- 反例：只统计创建 task 数；它不是并发运行数。

## P2：队列满的明确行为（B2）

- 输入：`max_in_flight=1,max_queue=1`，第三个请求抵达。
- 预期：按 `capacity_contract` 固定返回 429 或 503；response/trace 有同一 error code；不会无限等待。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_backpressure.py`。
- 反例：queue full 后 500 或 silent drop；测试应拒绝。

## P3：可重复 JSONL（B3）

- 起始：`generate_workload(seed,count)`。
- 输入：seed=7,count=3；seed=7,count=3；seed=8,count=3。
- 预期：前两次 bytes 完全相同，第三次允许不同；每行符合 request schema 且无真实文本。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_workload.py`。
- 反例：写入当前时间/uuid；应破坏确定性并被测试捕捉。

## P4：P95 算法（B4）

- 输入：`[]`、`[10]`、`[4,1,3,2,5]`，并声明 nearest-rank 定义。
- 预期：空输入明确 ValueError/None；其余精确匹配手算分位。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_statistics.py`。
- 反例：未排序就取索引；使用乱序案例捕捉。

## P5：固定压力矩阵（B5）

- 输入：in-flight=1/2/4 × slow/burst × repeat=3，固定 fake service time/seed。
- 预期：每请求 raw JSONL，汇总 P50/P95、accepted/rejected/cancelled、peak queue；报告声明模拟限制。
- 验收：`backpressure_report.md` 可指向 raw 文件；不得只保留平均值。
