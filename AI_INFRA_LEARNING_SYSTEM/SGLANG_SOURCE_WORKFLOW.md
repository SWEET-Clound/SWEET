# SGLang 源码学习工作流：从网页到可复现证据

Week 3–12 的 SGLang 练习不是“额外 2 小时阅读”，而是嵌入每周核心 10 小时的四个微循环：Session 1 锁版本/定位（20–30 分钟）；Session 2 读一个窄符号路径（30–40 分钟）；Session 3 collect/run 最小测试（20–30 分钟）；Session 4 写映射、反例、未证明项（30–40 分钟）。Session 5 只审计证据，不额外堆时间。

## 0. 唯一版本锁（Week 3 只做一次）

在独立目录 clone；不要使用当前 Agent venv，也不要在学习期间 `pull`：

```powershell
New-Item -ItemType Directory -Force external | Out-Null
git clone https://github.com/sgl-project/sglang.git external/sglang
git -C external/sglang fetch --tags --force
git -C external/sglang tag --sort=-v:refname | Select-Object -First 10
```

从输出中手动选择一个 stable release tag，写入变量；随后执行：

```powershell
$selectedRef = '<replace-with-one-release-tag-from-the-list>'
git -C external/sglang checkout --detach $selectedRef
git -C external/sglang status --short
git -C external/sglang rev-parse HEAD
git -C external/sglang describe --tags --always
git -C external/sglang show -s --format=%aI HEAD
git -C external/sglang submodule status
```

只有 `status --short` 空、HEAD 为完整 SHA 时才继续。填写 `sglang_notes/source_lock.json`：

```json
{
  "repository_url": "https://github.com/sgl-project/sglang.git",
  "selected_ref": "REPLACE_WITH_RELEASE_TAG",
  "immutable_commit_sha": "REPLACE_WITH_40_CHAR_SHA",
  "git_describe": "REPLACE_WITH_OUTPUT",
  "commit_author_date": "REPLACE_WITH_git_show_output",
  "checkout_date": "YYYY-MM-DD",
  "remote_url": "REPLACE_WITH_git_remote_v_output",
  "submodule_status": "REPLACE_WITH_OUTPUT",
  "source_environment": "Windows/WSL, Python version, no model server started",
  "dirty_worktree": false
}
```

网页和 `main` 只用于发现文档；任何源码事实必须写为 `SHA:path:line-range` 或 `SHA:path:symbol`。源码版本与网页内容不同时，以 source lock 为准并记录差异。

## 1. 测试等级与不可越级规则

| 等级 | 允许的活动 | 能证明 | 不能证明 |
| --- | --- | --- | --- |
| L0 静态定位 | `rg`、读取符号、调用图 | 该版本有该文本/符号 | 运行时路径 |
| L1 collect-only | `pytest --collect-only -q <path>` | 测试可被发现 | 测试通过/功能正确 |
| L2 CPU unit | 独立 venv 下最小 CPU test | 该 case 在该环境通过 | GPU/server 性能 |
| L3 GPU integration | 明确 GPU/model 预算的测试 | 窄 E2E 行为 | 普适引擎结论 |

Week 3–12 最低是 L1；L2 仅在 CPU-safe 且环境已记录时做。L3、模型下载和 server 启动不是本阶段必需条件。

## 2. SGLang 练习的通用评分

每个 `SGL-xx` 按 0/1/2 评分：问题范围窄、不可变 SHA、至少两个符号/行证据、真实测试命令/输出、本地练习映射、未证明项。12 分至少 9 分才进入下一周源码主题；否则回补证据，不能扩大阅读范围。

## 3. 三条源码视角

```text
协议与请求： API / streaming → request object → scheduler
执行与模型： scheduler → model runner → layers / attention
内存与复用： request lifecycle → KV cache → prefix/session radix cache
```

Week 3–12 对这三条线做受控取样，而非宣称读完项目。
