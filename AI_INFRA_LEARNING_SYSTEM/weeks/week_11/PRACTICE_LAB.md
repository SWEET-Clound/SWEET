# Week 11 练习实验室

统一使用 DETAILED_PATH 的默认 `DecoderConfig`；若为手算缩小配置，命名 `handcalc_config`，不得混用为 benchmark config。

## P1：config 与输入 contract（B1）

- 输入：默认 config；`d_model=30,n_heads=4`、负 vocab、空/浮点 token、超过 max seq。
- 预期：非法 config/input 明确 ValueError；合法 `[B,T] LongTensor` → logits `[B,T,V]`。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/decoder_lab/tests/test_config.py labs/decoder_lab/tests/test_forward.py`。
- 反例：隐式接受 float token 或先在 embedding 深处报模糊错误。

## P2：deterministic block forward（B2）

- 输入：fixed seed、`model.eval()`、dropout=0，B=2 fixed token IDs；仅改变 batch0。
- 预期：重复 forward equal、logits finite、batch1 不变、input tensor 未被原地改写。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/decoder_lab/tests/test_forward.py labs/decoder_lab/tests/test_determinism.py`。
- 反例：遗漏 residual 或仍处 train；至少一条测试应失败。

## P3：greedy generate 边界（B3）

- 输入：`max_new_tokens=0`、stub logits 立即 EOS、N=3、token out of range、prompt overflow。
- 预期：0 不变；EOS 不继续；N 时精确增加 N；非法 prompt 明确拒绝；输入不变。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/decoder_lab/tests/test_generate.py`。
- 提示：对 EOS 使用可控 stub/head，而不是赌随机权重刚好产生 EOS。

## P4：本地 timing 语义（B4）

- 输入：固定 config/prompt/seed，记录 prefill、first-token compute、后续 decode forwards、total。
- 预期：raw 行含 metric definition；报告明确这些不是端到端 TTFT；repeat≥5。
- 反例：将 Python 启动/打印/模型创建计入 forward；报告应标 measurement invalid。

## P5：no-cache replay 基线（B5）

- 输入：seed、config hash、prompt token IDs、max tokens。
- 预期：`decoder_replay.jsonl` 重跑输出 token IDs 相同；保存每步 logits/tokens 的可比较摘要。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/decoder_lab/tests/test_determinism.py`。
- 产物：为 Week12 cache/no-cache 对照提供 golden baseline；本周不得添加 cache。
