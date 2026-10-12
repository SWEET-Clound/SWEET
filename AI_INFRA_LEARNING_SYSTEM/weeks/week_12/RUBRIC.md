# Week 12 Gate 0B 验收

硬门槛：

- [ ] cache/no-cache 在定义容差内一致；
- [ ] cache shape/dtype/device/length 不变量有测试；
- [ ] matrix 每点重复 5 次并保留 raw data；
- [ ] 分开报告 prefill、TTFT、TPOT、total、peak memory；
- [ ] 至少一个收益不明显/失败点得到解释；
- [ ] 报告明确本实现不是生产 serving cache。

评分：理论与不变量 20，正确性 30，实验设计 20，raw data/报告 20，SGLang 映射 10。全部硬门槛且 80 分才通过。

口试：cache 避免哪种重复计算？未避免哪种？为什么增显存？local 与 agent session cache 增加什么正确性问题？
