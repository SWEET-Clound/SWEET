# Week 10 练习实验室

## P1：固定手算 contract（B1）

- 输入：B=H=1,T=2,D=1，`Q=[1,2]`,`K=[1,3]`,`V=[10,20]`；scale=`sqrt(1)`。
- 预期：在测试注释手算 scores、causal mask、softmax、两项 output；函数输出用明确近似断言匹配。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/attention_lab/tests/test_reference_attention.py`。
- 反例：`Tq!=Tk`、B/H/D 不匹配必须 ValueError，本周不假装支持 decode。

## P2：因果可见性性质（B2）

- 输入：复制 P1，再仅改 future V/K（index=1）。
- 预期：output index=0 不变；改 current/past V 会影响可见 output；no-causal 路径不同。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/attention_lab/tests/test_causal_property.py`。
- 反例：漏 mask 或 softmax 维度错；mutation 必使性质测试红。

## P3：multihead 与 SDPA oracle（B3）

- 输入：C=8,H=2 的 split/merge；H=1 equivalence；CPU FP32/dropout=0 fixture。
- 预期：C%H!=0 ValueError；reference 与 SDPA 在记录版本/atol/rtol 下 close。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q labs/attention_lab/tests/test_multihead.py labs/attention_lab/tests/test_sdpa_oracle.py`。
- 反例：直接套非方形 `is_causal`；本周不允许。

## P4：T² 测量（B4）

- 输入：T=16,32,64,128（资源不足可缩小，原样记录），固定 B/H/D、warmup/repeats。
- 预期：`complexity.csv` 有 raw timing 和 T² 列；报告将理论与实测分开。
- 反例：只用四点拟合就声称真实 serving 性能。

## P5：failure catalog 演练（B5）

- 输入：mask transpose、漏 scale、`view` 非 contiguous、softmax dim 错四种最小 mutation。
- 预期：每种对应至少一个红测试和恢复说明；新增失败能查表定位。
- 产物：`docs/failure_catalog.md` 带“症状→原因→诊断→修复→回归测试”。
