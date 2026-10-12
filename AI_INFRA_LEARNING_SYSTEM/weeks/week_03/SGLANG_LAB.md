# Week 3 SGLang Lab：仓库、版本、测试地图

**时间：核心 2h，深化 1h。** 目标不是安装整个服务或读完源码，而是建立可复现的源码入口。

## 进入与固定版本

在仓库根目录执行（若网络/权限受限，记录后只做网页阅读卡）：

```powershell
New-Item -ItemType Directory -Force external | Out-Null
git clone https://github.com/sgl-project/sglang.git external/sglang
git -C external/sglang rev-parse HEAD
git -C external/sglang status --short
```

把输出 SHA 和日期写入 `sglang_notes/pinned_revision.md`。后续只说“该 SHA”，不能称为永久事实。若目录已存在，**不 pull**；只记录当前 SHA，避免学习证据漂移。

## 任务路径

1. 阅读官方贡献指南的 setup、tests、PR 小节。回答：测试如何分类？变更前为何要跑最小相关测试？什么不应提交？
2. 在 clone 中用 `rg -n "pytest|test_" pyproject.toml docs tests` 定位测试入口；不需要一次安装所有 optional dependency。
3. 画一张 repo map：`python/`、`test/` 或 `tests/`、`docs/`、`benchmark/`（以实际路径为准），每项仅写一句职责。
4. 选择一个**无需 GPU 的纯 Python 测试**；先用 `pytest --collect-only -q <path>` 确认收集，再尝试运行。若环境未就绪，保存完整错误，不能把“收集失败”写成通过。
5. 写 `sglang_notes/w03_contribution_map.md`：SHA、命令、测试结果、一个 import/测试发现，以及“本项目 quality gate 与 SGLang 测试分层”的相同/不同。

## 最小验收与失败分支

- 通过：固定 SHA、repo map、一个 collect/run 证据、阅读卡和一个问题。
- clone 失败：保存 URL、时间、错误；阅读官方贡献页，记录缺少的动作。
- 测试依赖缺失：只建立收集/依赖图，标为 `BLOCKED_ENV`；不要在当前 Agent venv 安装 SGLang 依赖。
- Stop line：不要在本周研究 scheduler、cache 或 GPU server，它们属于后续 Lab。
