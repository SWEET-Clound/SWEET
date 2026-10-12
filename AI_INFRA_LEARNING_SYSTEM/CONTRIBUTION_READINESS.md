# Week 12 后的 SGLang 贡献就绪检查

只有同时满足下列项目，才从 Week 13+ 开始 issue 复现、patch 或与维护者互动；否则继续补证据，不以“我看过源码”冒进。

- [ ] `source_lock.json` 有 release ref、40 位 SHA、detached/clean 证据；
- [ ] `SGLANG_TEST_MATRIX.md` 至少有 10 行真实 L1/L2 记录或明确环境阻塞；
- [ ] API→scheduler、scheduler→model、request→cache 至少各有一张引用 `SHA:path:symbol` 的图；
- [ ] 一个 local analogue 有 CPU 正确性测试；
- [ ] 选定问题有最小输入、不变量、预期、风险、现状 `REPRODUCED` 或 `NOT_REPRODUCED`；
- [ ] 已写出不声称的范围（GPU、并行、模型、版本、性能等）；
- [ ] 没有把个人/敏感请求数据放进 issue、trace、commit 或 benchmark。

通过并不保证 PR 会合并；它仅表示你能提出可审查、可重放、范围受控的问题。
