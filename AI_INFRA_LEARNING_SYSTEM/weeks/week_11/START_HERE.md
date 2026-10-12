# Week 11 从这里开始

本周把已经验证的 attention 放进最小 Decoder。重点是解释 prefill 与逐 token decode 的数据流并建立 no-cache 基线；不下载大模型、不追求语言质量。

进入 Gate：Week 10 attention reference 对照通过。模型规模保持小、固定 seed、eval/dropout=0；否则 KV cache 的一致性比较没有可信 oracle。

本周结束时你应能分别报告 prefill、first-token、per-token、total time 和 peak memory，而不混成“平均速度”。
