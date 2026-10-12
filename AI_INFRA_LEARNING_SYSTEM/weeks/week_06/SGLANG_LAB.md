# Week 6 SGLang Lab：scheduler 指标与 benchmark JSONL

**时间：核心 2h。**

1. 在固定 SHA 的 docs/benchmark 目录定位 serving benchmark 的输入格式、concurrency/request-rate、TTFT/ITL/TPOT 和 raw output 说明。
2. 写 `sglang_notes/w06_metric_mapping.md`：本地 `queue_wait_ms/service_ms` 与 serving 指标对应/不对应的地方；特别注明本地 fake service **没有**模型 TTFT。
3. 找 scheduler 或 benchmark 的一个输入 schema/测试，执行 collect-only；记录命令、SHA、结果。
4. 将本地 workload JSONL 附一个 data card：synthetic、seed、长度分布、共享前缀比例、cancel 规则、不能代表的真实负载。

**验收**：指标映射、真实路径/符号、collect/run、数据卡。禁止把 session ID 当作真实用户 ID。
