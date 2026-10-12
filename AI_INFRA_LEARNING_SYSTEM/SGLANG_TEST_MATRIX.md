# SGLang 源码测试矩阵

每周执行 SGLang Lab 后追加一行。`BLOCKED_ENV` 是有效状态，但必须带第一条真实错误；不得用猜测填 PASS。

| Week / SGL exercise | source revision | level L0–L3 | discovered `path:symbol` | command | exit code | environment | status | 此结果证明 | 尚未证明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 03 / SGL-03-C | `<SHA>` | L1 | `<path:symbol>` | `pytest --collect-only -q <path>` | `<code>` | `<OS/Python>` | `PASS/FAIL/BLOCKED_ENV` | `<fact>` | `<limit>` |

## 填写规则

1. revision 必须等于 `source_lock.json` 的 immutable SHA。
2. path 必须来自该 checkout；网页路径不可代替。
3. command 必须可复制，禁止“运行相关测试”。
4. 若依赖缺失，保留命令和完整第一错误，将 status 写 `BLOCKED_ENV`。
5. 实际运行输出可保存于 `sglang_notes/test_outputs/wNN.txt`，矩阵只保存摘要与路径。
