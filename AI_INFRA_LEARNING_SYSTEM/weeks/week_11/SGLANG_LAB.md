# Week 11 SGLang Lab：prefill/decode 与 scheduling policy

**时间：核心 2h。**

1. 在固定 SHA 找 `schedule_policy.py` 和相关请求/队列符号，以源码证据区分 waiting、prefill、decode（若版本命名不同，记录实际名称）。
2. 写 `sglang_notes/w11_prefill_decode_map.md`：本地 decoder 的 full prompt/one-token step 映射到上游概念；标明 queue batching、cache sharing、network latency 不是本地实验所证明的。
3. 用 `rg -n "prefill|decode|waiting|priority"` 找一条决策分支，画不超过 8 节点决策图。
4. 对对应测试 collect/run，并记录 SHA、命令、结果。

**验收**：术语证据、决策图、测试证据、至少两个不可类比点；不能把 local first-token compute 写成 SGLang TTFT。
