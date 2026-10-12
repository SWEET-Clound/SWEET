# Week 3–12 板块—练习清单

每一个 `Mxx-P1/P2/P3` 都是一个小闭环：**P1 预测（10–15 分钟）→ P2 引导实现（30–45 分钟）→ P3 独立反例/迁移（20–45 分钟）**。精确输入、命令、反例、提示、证据位置以每周 `PRACTICE_LAB.md` 为准；没有完成 P3 不算掌握该模块。

| Week | 模块 | P1 预测题 | P2 引导题 | P3 独立/反例题 | 证据 |
| ---: | --- | --- | --- | --- | --- |
| 3 | M1 边界 | 责任归属 | ADR+characterization | import side effect | ADR/tests |
| 3 | M2 纯模块 | 依赖方向 | router 抽取 | 循环 import 反例 | routing tests |
| 3 | M3 类型 | reveal/type 预测 | bad route 红绿 | Protocol 迁移 | mypy output |
| 3 | M4 输入 | 边界表 | 参数化测试 | secret leak 反例 | tests |
| 3 | M5 质量 | 工具预测 | mutation fixes | 一条 gate | audit |
| 4 | M1 schema | 四张 JSON 卡 | contract test | 200+error 反例 | API tests |
| 4 | M2 ID/log | 隐私分类 | fixed-ID tests | two-request isolation | JSONL |
| 4 | M3 health | live/ready 表 | fake unavailable | no-provider-call | health tests |
| 4 | M4 service | fake vs mock | 4 fake injection | network guard | tests |
| 4 | M5 review | evidence inventory | trace schema | sensitive-field mutation | report |
| 5 | M1 async | await/task order | Event timeout | cancel re-raise | tests |
| 5 | M2 SSE | state machine | encoder | malformed frame | tests |
| 5 | M3 stream | a/b/c frames | fake generator | source error | raw stream |
| 5 | M4 registry | lifecycle graph | 4 operations | residual task | tests |
| 5 | M5 evidence | field selection | validator | duplicate terminal | trace |
| 6 | M1 metrics | P95 handcalc | quantile function | small-sample limitation | tests |
| 6 | M2 admission | semaphore timeline | barrier test | permit leak | tests |
| 6 | M3 queue | strategy compare | full queue contract | cancel/slow consumer | tests |
| 6 | M4 workload | matrix pre-registration | seeded generator | schema validator | JSONL |
| 6 | M5 mapping | HOL timeline | fault experiment | production-boundary report | report |
| 7 | M1 trace | field card | success tree | unclosed span | tests |
| 7 | M2 faults | mapping matrix | deterministic fake | response/span mismatch | tests |
| 7 | M3 retry | allowlist choice | fake clock retry | side-effect call count | tests |
| 7 | M4 diagnosis | trace questions | answer fixture | missing attribute | answer file |
| 7 | M5 RCA | label definition | one RCA | replay by another | report |
| 8 | M1 audit | evidence level | index fill | missing raw blocks Gate | index |
| 8 | M2 stability | run pre-registration | 3-run runner | injected failure | JSONL |
| 8 | M3 clean env | install prediction | fresh venv | doc defect | rebuild log |
| 8 | M4 data card | required field card | lint | prompt leak | data card |
| 8 | M5 decision | criteria | independent score | red item blocks | Gate |
| 9 | M1 env | capability table | smoke record | Windows/WSL confusion | environment |
| 9 | M2 tensor | shapes | properties | unstable softmax | tests |
| 9 | M3 dtype | bytes estimate | error ledger | implicit migration | CSV |
| 9 | M4 measure | timing plan | 30 raw runs | min-only report | CSV/trace |
| 9 | M5 report | graph claim | report | one-variable rerun | report |
| 10 | M1 maths | BHTD card | handcalc | Tq≠Tk rejection | tests |
| 10 | M2 causal | mask direction | future mutation | missing mask | tests |
| 10 | M3 heads | split/merge | H=1 oracle | invalid C/H | tests |
| 10 | M4 cost | O(T²) | raw matrix | production claim | CSV |
| 10 | M5 failure | symptom match | catalog | new failure lookup | catalog |
| 11 | M1 config | parameter/shape | validation | invalid token | tests |
| 11 | M2 forward | invariants | eval deterministic | residual omission | tests |
| 11 | M3 generate | token timeline | EOS/max test | mutation/overflow | tests |
| 11 | M4 timing | metric names | raw timing | TTFT misuse | report |
| 11 | M5 baseline | no-cache record | replay | cache seam only | JSONL |
| 12 | M1 contract | byte derivation | cache shape | foreign cache | tests |
| 12 | M2 equivalence | state diagram | step logits | wrong concat axis | tests |
| 12 | M3 boundaries | case matrix | B2/EOS/context | cross-request leak | tests |
| 12 | M4 benchmark | pre-register | raw CSV | remove OOM | report |
| 12 | M5 Gate | mapping table | independent rerun | unsupported claim | Gate |
