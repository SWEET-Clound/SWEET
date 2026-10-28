# Week 2 学习记录

只记录可检查的证据，不写“感觉学会了”。

## W2-0

- 日期：
- Python 命令：
- pytest 原始结果：
- 预测与实际：
- 此测试证明：
- 此测试未证明：

## W2-1 代码地图

| 位置 | 作用               | 导入时执行？                                            | 外部依赖？ | 我的解释          |
| --- |------------------|---------------------------------------------------|-------|---------------|
| `os.getenv` | 获取环境变量           | import os     | 不是    | python安装自带的模块 |
| `init_chat_model` | 初始模型配置           | from langchain.chat_models import init_chat_model | 是     | 从模块中导入函数      |
| `calculator` | 计算器工具            | from study_assistant import  calculator   | 不是    | 从模块中导入方法      |
| `search_local_knowledge` | 本地资料检索工具         |  from study_assistant import search_local_knowledge | 不是    | 从模块中导入方法      |
| `router_node` | 节点方法             |   from study_assistant import router_node          | 不是    | 从模块中导入函数      |
| `route_by_task_type` | 路由函数             |    from study_assistant import route_by_task_type  | 不是    | 从模块中导入函数      |
| `builder.compile` | StateGraph模块下的方法 |    from langgraph.graph import StateGraph          |       |               |
| `run_agent` | 运行智能体  |   from study_assistant import run_agent    | 不是    | 是模块中的方法       |

我预测的首个失败点：

## W2-2 / W2-3 契约与测试

| 场景 | 预期 | 实际 | 测试名 |
| --- | --- | --- | --- |
| Key 不存在 |  |  |  |
| Key 空白 |  |  |  |
| Key 有值 |  |  |  |
| `2 + 3 * 4` |  |  |  |
| 空白算式 |  |  |  |
| `name + 1` |  |  |  |
| `2 ** 100` |  |  |  |
| `1 / 0` |  |  |  |

第一次 Red 测试与失败原因：

`rg -n "eval\\(" Agents` 结果：

## W2-4 / W2-5

- 四类错误对应测试：
- 路由离线测试命令与结果：
- `bind_tools` 静态 trace：输入 →
- 直接依赖与已验证版本：
- `.env.example` 只有占位符：是 / 否
- 新终端离线测试：
- 哪一步真实调用模型：

## W2-6 复盘

- 全量测试命令：
- 通过 / 失败 / 跳过：
- 测试总数 ≥10：是 / 否
- 已移除 `eval`：是 / 否
- 能独立解释的概念（至少 4 项）：
- 最困惑的一项：
- 下周前不可跳过的阻塞：
