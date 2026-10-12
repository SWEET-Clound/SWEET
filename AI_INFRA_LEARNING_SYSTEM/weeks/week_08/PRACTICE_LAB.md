# Week 8 练习实验室

## P1：证据索引（B1）

- 起始：`evidence/index.md`。
- 输入：W3–W7 每周至少一个 claim。
- 预期字段：`claim, exercise_id, command, exit_code, raw_path, revision, environment, limitation, status`。
- 验证：人为删除 raw_path 后审计应标 `BLOCKED`，不能继续 Gate。
- 反例：只写“pytest passed”没有测试/命令；索引无效。

## P2：三次稳定 runner（B2）

- 起始：`scripts/stability.ps1 -Runs 3`。
- 输入：quality command 或固定小 pytest command。
- 预期：输出三条 JSONL，含 UTC、run、exit_code、duration_ms；任一次非零则 script 非零。
- 命令：`powershell -ExecutionPolicy Bypass -File scripts/stability.ps1 -Runs 3`。
- 反例：故意传一个失败命令，验证总 exit 非零且保留失败行。

## P3：干净 venv 重建（B3）

- 输入：`requirements-dev.txt` 或锁文件及其 hash；全局 Python 不可作为依赖来源。
- 预期：新环境中安装、pytest、quality 的真实输出；失败保留第一错误/依赖版本。
- 反例：因全局已装包通过；在空 venv 中应揭露。
- 提示：计划先写 install command；实际执行只在本周，不提前删除旧环境。

## P4：数据卡 lint（B4）

- 起始：`workloads/agent_cache_lab/DATA_CARD.md` 与测试/简单检查器。
- 必需 headings：来源、synthetic/真实、字段、脱敏、许可、保留删除、偏差、禁止用途。
- 预期：缺任何 heading 即失败；不得含完整 prompt/身份字段。
- 命令：`.\.venv\Scripts\python.exe -m pytest -q tests/test_data_card.py`。

## P5：Gate 0A 判定（B5）

- 输入：三套证据情景（所有硬项绿；缺 raw；clean venv 失败）。
- 预期：分别判 continue/extend/extend，理由引用 P1 状态；不能仅按总分。
- 产物：`reviews/YYYY-W08-gate.md`，列红项、回补时间和不可声称的能力。
