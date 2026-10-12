# Week 10 SGLang Lab：attention backend 接口

**时间：核心 2h。**

1. 在固定 SHA 的 `srt/layers` 或实际 attention backend 目录寻找接口/类，最多读三个符号。
2. 写 `sglang_notes/w10_attention_question.md`：Q/K/V shape 的证据、backend 选择线索、mask/cache 的一个未回答问题；每条标文件与行/符号。
3. 运行一个相关 test collect-only（可运行则保存结果）。
4. 做映射表：本地 full prefill reference 能证明什么；SGLang backend 中哪些由 kernel、cache layout、分布式或量化决定，不能类比。

**验收**：引用固定 SHA 与符号，问题不是泛泛“attention 怎么更快”。
