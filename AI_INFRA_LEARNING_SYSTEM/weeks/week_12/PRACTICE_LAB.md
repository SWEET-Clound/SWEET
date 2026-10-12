# Week 12 练习实验室

CPU FP32、fixed config、`model.eval()` 是本周正确性 oracle。先跑 no-cache replay；任何差异先缩小为 B=1/H=1/T≤3。

## P1：cache contract 与拒绝路径（B1）

- 起始：`labs/decoder_lab/tests/test_cache_contract.py`。
- 输入：prefill `[1,3]`、decode `[1,1]`，wrong model/config/dtype/device cache。
- 预期：K/V `[B,H,T,D]`；prefill len=3、decode len 3→4；错误 owner/config 明确拒绝。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/decoder_lab/tests/test_cache_contract.py`。
- 反例：全局 cache 或允许 foreign request；tests 必须发现。

## P2：逐 token logits 等价（B2）

- 输入：fixed prompt 与 generated prefix；分别 no-cache full forward、prefill+one-token decode。
- 预期：每一步 logits 用严格 CPU FP32 `assert_close`；最终 tokens 相同。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/decoder_lab/tests/test_cache_correctness.py`。
- 反例：临时把 concat axis 改为 `-1`；准确的 shape/logit invariant 必须失败，修复后绿。

## P3：边界矩阵（B3）

- 输入：T=1、短/长、B=2 isolation、多 layer、EOS、max context、new request `None` cache。
- 预期：每行矩阵都有单独 test/状态；B=2 中改 batch0 不影响 batch1；新请求不复用旧 cache。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/decoder_lab/tests/test_cache_correctness.py`。
- 反例：跨请求 cache 污染；测试应红。

## P4：预注册 benchmark（B4）

- 输入：context `16/64/256/512` × output `1/16/64` × repeats=5；资源不足可缩小，但先写 reduced matrix 与理由。
- 预期：raw CSV 对每 mode 保留一行/重复；OOM 也保留行；图只从 raw CSV 生成。
- 验证：检查完整矩阵或标明缩减；`output_len<2` 的 TPOT=`NA`。
- 反例：删除 OOM 或只存汇总；审计失败。

## P5：local 到 runtime 的边界（B5）

- 输入：local K/V cache、SGLang radix/session cache、vLLM paged cache。
- 预期：表中列本周已证明、未证明、下一问题；Gate0B 独立复跑 P2/P4 并列限制。
- 产物：`kv_cache_report.md` 与 `Gate0B` 状态；无 raw/independent rerun 不能通过。
