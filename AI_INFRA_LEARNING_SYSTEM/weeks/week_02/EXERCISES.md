# Week 2 练习册

统一顺序：**预测 → 写契约 → 让测试失败 → 最小实现 → 运行 → 用一句话解释。** 卡住超过 20 分钟时，把预测、代码、报错和尝试发给我；我只补当前下一步。

## E1：代码地图

读 `Agents/study_assistant.py`，至少填 8 行，必须含 `os.getenv`、`init_chat_model`、`calculator`、`router_node`、`builder.compile`、`run_agent`。

| 代码/函数 | 做什么 | 导入时执行？ | 外部环境/服务？ | 以后归属 |
| --- | --- | --- | --- | --- |
| `router_node` |  | 否 | 否 |  |
| `model = init_chat_model(...)` |  | 是 | 是 |  |
|  |  |  |  |  |

自检：函数定义会被创建但函数体不会运行；赋值右侧会运行；`builder.compile` 是构建动作；路由、算术、文案适合独立无模型模块。

## E2：配置契约

先填表，才写代码。

| Key 状态 | `get_required_env("DEEPSEEK_API_KEY")` 预期 | 可见信息 |
| --- | --- | --- |
| 不存在 |  |  |
| `"   "` |  |  |
| `"demo-key"` |  |  |

约束：定义 `ConfigurationError`；不打印值；测试不依赖你真实环境；用 `monkeypatch.delenv` / `setenv`；每个测试只证明一种行为。

最小骨架：

```python
import pytest
from Agents.config import ConfigurationError, get_required_env

def test_missing_required_setting_is_clear(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        get_required_env("DEEPSEEK_API_KEY")
```

达成：3+ 测试通过，测试结束后环境恢复，错误不含真实值。

## E3：安全计算器

先填行为表。

| 输入 | 预期 | 原因 |
| --- | --- | --- |
| `2 + 3 * 4` |  |  |
| `(10 - 4) / 2` |  |  |
| `-5 + 8` |  |  |
| 空白 |  |  |
| `name + 1` |  |  |
| `__import__('os')` |  |  |
| `2 ** 100` |  |  |
| `1 / 0` |  |  |

检查单：

- [ ] `ast.parse(..., mode="eval")`
- [ ] 只接受数字 `Constant`、`BinOp`、`UnaryOp`
- [ ] 只允许 `+ - * /` 与一元 `+/-`
- [ ] 拒绝名字、函数调用、属性、`**`
- [ ] 拒绝空白与超过 200 字符
- [ ] 除零变成稳定的输入错误
- [ ] 没有 `eval` 或 `exec`
- [ ] 每行行为有测试

Red–Green–Refactor：先写 `test_rejects_name` 并确认失败；最小实现让它通过；再写 `test_calculates_operator_precedence`；两者通过后再整理重复逻辑。

达成：8+ 测试，`rg -n "eval\\(" Agents` 无 Agent 源码命中。

## E4：错误分类

填入输入错误、配置错误、依赖故障、内部错误，并写安全公开文案。

| 场景 | 类别 | 谁先行动 | 公开提示 |
| --- | --- | --- | --- |
| 用户提交空算式 |  |  |  |
| Key 不存在 |  |  |  |
| provider 超时 |  |  |  |
| 应支持却未处理的 AST 节点 |  |  |  |
| 用户写 `2 ** 100` |  |  |  |
| DNS 不可用 |  |  |  |
| 找不到已声明模板 |  |  |  |
| `name + 1` |  |  |  |

自检：用户改输入的是输入错误；操作者补环境的是配置错误；网络/provider 是依赖故障；程序承诺支持却没处理的是内部错误。`2 ** 100` 在本周契约中是输入错误。

达成：每类至少一项 pytest；公开文案不含秘密、绝对路径或 traceback。

## E5：路由离线测试

只测“输入 → task_type”。若导入主文件仍创建模型，先提取不依赖 LangChain 的纯路由规则，不能用真实 Key 绕过。

| 输入 | 预期 |
| --- | --- |
| `2 + 3 等于多少？` | `tool` |
| `帮我制定三天学习计划` | `study_plan` |
| `bind_tools 有什么作用？` | `local_knowledge` |
| 空白 | `unknown` |
| `你好` | `chat` |

达成：5 个独立、命名清楚的测试，不联网、不读 Key；若关键词冲突，写下优先级并为它测试。

## E6：复现演练

为“没看过项目的人”写清晰步骤：进入仓库、创建/激活 venv、安装开发依赖、运行离线测试、复制 `.env.example` 并设置本地 Key、哪一步才会调用真实模型。

达成：新终端无需猜 Python 路径即可跑离线测试；真实 Key 从未进入 Git 文件或终端截图。
