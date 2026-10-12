# Week 5 练习册

## E1：先预测 event loop

比较顺序 await 与两个 `create_task`，先写预计输出顺序/耗时，再运行记录。

## E2：timeout cleanup

写 `slow_tool`，在 finally 设置可观察标记。先写 deadline 超时测试：得到稳定错误且标记为真。

## E3：取消传播

创建 task → cancel → await；记录 `CancelledError` 经过哪里。再写一个“吞掉取消”的反例并解释为何删除。

## E4：SSE contract

为 `start/delta/done/error` 写字段表：event、request_id、sequence、data。测试 sequence 单调、done 只有一个、错误后无第二个 done。

## E5：断开模拟

让 consumer 提前停止；断言 producer finally 执行、子 task 已结束、队列不再增长。

## E6：慢消费者时间线

画 producer/queue/consumer/cancel 四条线，标 queue 满时的 chosen policy。
