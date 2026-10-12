# Week 8 逐次执行手册：可复现工程审计与继续决策

> 核心 10h + 深化 5h。目标是把前 7 周变成第三人能检验的证据包，并决定是否进入模型实验。

## 进入 Gate

列出 W3–W7 每周硬产物；不存在就标红，不凭记忆补“已完成”。本周不引入新框架和功能。

## Session 1（2h）：干净环境重建

1. 在新的目录/venv 执行项目 README 的安装步骤；记录 Python 版本、lock/requirements hash、操作系统。
2. 只安装明示的 dev dependencies，运行完整 pytest 与 quality gate。
3. 重建失败时保存第一条错误、依赖树和修复过程；不得手工从全局环境偷包后称成功。

## Session 2（2h）：稳定性 runner

创建 `scripts/stability.ps1`（或 documented equivalent）：连续 N=3 次运行 `quality`/tests，输出每次 UTC 时间、exit code、duration 到 JSONL。任何一次失败即总失败。

测试 runner 对故意失败命令返回非零；随后移除故意失败。N=3 是可靠性信号，不是统计学性能证明。

## Session 3（2h）：证据索引与风险审计

建立 `evidence/index.md`：每项的 claim、命令、raw 文件、revision/环境、测试名、限制。逐项审计：API、stream/cancel、queue、trace、secret hygiene、复现。

## Session 4（2h）：workload 数据卡与 issue 预筛

完善 `workloads/agent_cache_lab/DATA_CARD.md`：synthetic 来源、字段、脱敏、许可、保留/删除、已知偏差、不可使用场景。选择一个 SGLang cache/scheduler issue，仅做“是否可复现/能否写 CPU test/范围多大”的卡片，不能直接认领或留言。

## Session 5（2h）：Gate 0A 决策

写 `reviews/YYYY-W08-gate.md`：

```text
继续：核心 tests+稳定 runner 通过，能解释 trace/queue/cancel；
延长 1–2 周：工程正确但复现或一个硬项缺失；
转向/缩小：持续无法获得稳定环境或不享受该类调试（附事实）。
```

结论必须引用 evidence index，不是情绪投票。完成 [SGLang Lab](SGLANG_LAB.md)。

## 深化与完成定义

让另一台机器/未来的你按 README 冷启动一次；创建复盘演示脚本。完成需 clean venv、3 次稳定结果、evidence index、数据卡、Gate 0A 和所有红项的回补计划。
