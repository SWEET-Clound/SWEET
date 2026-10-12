# Week 6 练习册

## E1：P95 手算

对 10 个、20 个延迟样本排序，计算 median/P95；解释为何小样本分位数不稳定。

## E2：Semaphore 时间线

5 请求、2 permits、每服务 100ms。先画开始/结束，再运行 fake service，断言 in-flight 最大值 ≤2。

## E3：异常 permit

worker 抛异常后，后续请求仍能进入。写测试证明 permit 被释放。

## E4：队列策略

`Queue(maxsize=4)` 满时选择等待、拒绝或丢弃之一；写用户语义、HTTP 语义、测试。不得静默丢失 Agent 请求。

## E5：实验矩阵

先填写假设，再跑 1/5/10/20 并发 × 5 次；每点保存原始 JSONL。

## E6：head-of-line blocking

注入一个慢请求，比较 FIFO 下其他请求的等待。写出 Week 7 需要 trace 的字段。
