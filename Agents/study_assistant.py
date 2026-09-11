# 基于langgraph的个人学习助手智能体
# 支持调用真实大模型，工具调用，条件路由，记忆，流式输出，错误处理，任务规划和本地知识查询

import os
import operator
from typing import Literal, Optional, Annotated

from typing import TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AnyMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
)
from langchain_core.tools import tool

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import InMemorySaver
from openai.types.beta.beta_response_input_item import AgentMessage

# 配置环境变量和真实大模型
api_key = os.getenv("DEEPSEEK_API_KEY")

model  = init_chat_model(
    "deepseek-v4-pro",
    api_key=api_key,
    temperature = 0
)

# =========================
# 2. 定义工具
# =========================

@tool
def calculator(expression: str) -> str:
    """
    Calculate a simple arithmetic expression.

    Args:
        expression: A math expression, for example "3 + 4 * 10".
    """
    try:
        # 注意：真实生产环境不要直接 eval 用户输入。
        # 这里只作为学习 demo。
        allowed_chars = set("0123456789+-*/(). ")
        if not set(expression) <= allowed_chars:
            return "Error: expression contains unsupported characters."

        result = eval(expression)
        return str(result)

    except Exception as e:
        return f"Calculation error: {str(e)}"


@tool
def search_local_knowledge(query: str) -> str:
    """
    Search local LangGraph study notes.

    Args:
        query: User question about LangGraph.
    """

    knowledge_base = {
        "StateGraph": (
            "StateGraph 是 LangGraph 里用来构建状态图的核心类。"
            "你需要先定义 State，然后添加节点 add_node，添加边 add_edge 或 add_conditional_edges，"
            "最后 compile 成可运行 graph。"
        ),
        "MessagesState": (
            "MessagesState 是 LangGraph 预定义的消息状态，核心字段是 messages。"
            "它适合聊天 Agent，因为它可以保存 HumanMessage、AIMessage、ToolMessage 等消息。"
        ),
        "add_conditional_edges": (
            "add_conditional_edges 用来添加条件边。"
            "它会在某个节点执行结束后，调用路由函数，根据当前 state 判断下一步去哪个节点。"
        ),
        "bind_tools": (
            "bind_tools 的作用是把工具 schema 绑定给模型，让模型知道有哪些工具可以调用。"
            "它不会真正执行工具，真正执行工具通常由 ToolNode 或你自己写的 tool_node 完成。"
        ),
        "ToolNode": (
            "ToolNode 是 LangGraph 提供的预构建工具节点。"
            "它可以自动执行 AIMessage 里的 tool_calls，并把结果包装成 ToolMessage。"
        ),
        "stream": (
            "stream 可以让 LangGraph 流式输出。"
            "常见 stream_mode 有 updates、values、messages。"
            "updates 适合调试节点输出，messages 适合 token 级流式聊天。"
        ),
        "checkpointer": (
            "checkpointer 用来保存图的状态，实现短期记忆、断点恢复和多轮会话。"
            "使用 checkpointer 时，调用 graph.invoke 或 graph.stream 需要传 thread_id。"
        ),
    }

    query_lower = query.lower()

    matched = []
    for key, value in knowledge_base.items():
        if key.lower() in query_lower or query_lower in key.lower():
            matched.append(f"{key}: {value}")

    if matched:
        return "\n".join(matched)

    return (
        "没有找到完全匹配的本地笔记。"
        "你可以尝试查询 StateGraph、MessagesState、bind_tools、ToolNode、stream、checkpointer。"
    )


@tool
def make_study_plan(topic: str, days: int = 3) -> str:
    """
    Create a simple study plan.

    Args:
        topic: The topic the user wants to study.
        days: Number of days for the study plan.
    """
    plan = []
    for day in range(1, days + 1):
        plan.append(
            f"Day {day}: 学习 {topic} 的第 {day} 个核心部分，完成一个小代码练习。"
        )

    return "\n".join(plan)


tools = [
    calculator,
    search_local_knowledge,
    make_study_plan,
]

model_with_tools = model.bind_tools(tools)


# =========================
# 3. 定义复杂 State
# =========================

class AgentState(TypedDict):
    # messages 用 operator.add，表示新消息追加到旧消息后面
    messages: Annotated[list[AnyMessage], operator.add]

    # 用户原始输入
    user_input: str

    # 路由判断出来的任务类型
    task_type: str

    # 是否发生错误
    error: Optional[str]

    # LLM 调用次数
    llm_calls: int


# =========================
# 4. 定义节点
# =========================

def router_node(state: AgentState):
    """
    判断用户输入属于哪类任务。
    这里先用规则判断，方便你理解。
    后面可以换成 LLM 分类器。
    """
    user_input = state["user_input"]

    if any(keyword in user_input for keyword in ["计算", "+", "-", "*", "/", "等于"]):
        task_type = "tool"

    elif any(keyword in user_input for keyword in ["计划", "学习路线", "怎么学"]):
        task_type = "study_plan"

    elif any(keyword in user_input for keyword in ["StateGraph", "MessagesState", "bind_tools", "ToolNode", "stream", "checkpointer", "条件边"]):
        task_type = "local_knowledge"

    elif len(user_input.strip()) == 0:
        task_type = "unknown"

    else:
        task_type = "chat"

    return {
        "task_type": task_type,
        "llm_calls": state.get("llm_calls", 0),
    }


def chat_node(state: AgentState):
    """
    普通聊天节点。
    不强制用工具，只让真实大模型回答。
    """
    system_prompt = SystemMessage(
        content=(
            "你是一个严谨的 LangGraph 学习助手。"
            "回答要清晰、分层，适合正在学习 Agent 框架的学生。"
        )
    )

    response = model.invoke(
        [system_prompt] + state["messages"]
    )

    return {
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def llm_with_tools_node(state: AgentState):
    """
    带工具调用能力的 LLM 节点。
    模型可能直接回答，也可能产生 tool_calls。
    """
    system_prompt = SystemMessage(
        content=(
            "你是一个可以使用工具的 Agent。"
            "如果用户问题需要计算、查询本地知识或生成学习计划，请调用合适工具。"
            "如果不需要工具，请直接回答。"
        )
    )

    response = model_with_tools.invoke(
        [system_prompt] + state["messages"]
    )

    return {
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def study_plan_node(state: AgentState):
    """
    专门处理学习计划。
    这里演示：一个节点可以直接调用真实 LLM，不一定要走 tool_call。
    """
    user_input = state["user_input"]

    prompt = [
        SystemMessage(
            content=(
                "你是一个 Agent 框架学习导师。"
                "请为用户制定具体、可执行、由浅到深的学习计划。"
                "计划里必须包含每天的目标、代码练习和验收标准。"
            )
        ),
        HumanMessage(content=user_input),
    ]

    response = model.invoke(prompt)

    return {
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def knowledge_node(state: AgentState):
    """
    本地知识查询节点。
    这里为了演示，直接调用 search_local_knowledge 工具。
    真实 RAG 项目里，这里可以换成向量数据库检索。
    """
    query = state["user_input"]
    result = search_local_knowledge.invoke({"query": query})

    response = AIMessage(
        content=(
            "我从本地 LangGraph 学习笔记中查到：\n\n"
            f"{result}"
        )
    )

    return {
        "messages": [response],
    }


def fallback_node(state: AgentState):
    """
    兜底节点。
    """
    return {
        "messages": [
            AIMessage(content="我没有识别到明确任务，请重新描述你的问题。")
        ]
    }


def error_node(state: AgentState):
    """
    错误处理节点。
    """
    return {
        "messages": [
            AIMessage(content=f"执行过程中出现错误：{state.get('error')}")
        ]
    }


# =========================
# 5. 定义路由函数
# =========================

def route_by_task_type(
    state: AgentState,
) -> Literal["chat_node", "llm_with_tools_node", "study_plan_node", "knowledge_node", "fallback_node"]:
    """
    根据 task_type 决定下一步去哪个节点。
    """

    task_type = state["task_type"]

    if task_type == "tool":
        return "llm_with_tools_node"

    if task_type == "study_plan":
        return "study_plan_node"

    if task_type == "local_knowledge":
        return "knowledge_node"

    if task_type == "chat":
        return "chat_node"

    return "fallback_node"


def should_continue_after_tools(
    state: AgentState,
) -> Literal["tools", "__end__"]:
    """
    判断 LLM 是否产生了工具调用。
    如果有 tool_calls，进入 tools 节点。
    如果没有，结束。
    """
    last_message = state["messages"][-1]

    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"

    return END


# =========================
# 6. 构建 LangGraph
# =========================

builder = StateGraph(AgentState)

builder.add_node("router_node", router_node)
builder.add_node("chat_node", chat_node)
builder.add_node("llm_with_tools_node", llm_with_tools_node)
builder.add_node("study_plan_node", study_plan_node)
builder.add_node("knowledge_node", knowledge_node)
builder.add_node("fallback_node", fallback_node)
builder.add_node("error_node", error_node)

# ToolNode 是 LangGraph 预构建工具节点
builder.add_node("tools", ToolNode(tools))

builder.add_edge(START, "router_node")

builder.add_conditional_edges(
    "router_node",
    route_by_task_type,
    {
        "chat_node": "chat_node",
        "llm_with_tools_node": "llm_with_tools_node",
        "study_plan_node": "study_plan_node",
        "knowledge_node": "knowledge_node",
        "fallback_node": "fallback_node",
    },
)

builder.add_conditional_edges(
    "llm_with_tools_node",
    should_continue_after_tools,
    {
        "tools": "tools",
        END: END,
    },
)

# 工具执行完后，把结果交回 LLM
builder.add_edge("tools", "llm_with_tools_node")

builder.add_edge("chat_node", END)
builder.add_edge("study_plan_node", END)
builder.add_edge("knowledge_node", END)
builder.add_edge("fallback_node", END)
builder.add_edge("error_node", END)


# =========================
# 7. 加入 Checkpointer 记忆
# =========================

memory = InMemorySaver()

graph = builder.compile(checkpointer=memory)


# =========================
# 8. 封装运行函数
# =========================

def run_agent(user_input: str, thread_id: str = "demo-thread"):
    """
    普通 invoke 调用。
    """
    initial_state = {
        "messages": [HumanMessage(content=user_input)],
        "user_input": user_input,
        "task_type": "",
        "error": None,
        "llm_calls": 0,
    }

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    result = graph.invoke(initial_state, config=config)

    print("\n========== Final Messages ==========\n")
    for message in result["messages"]:
        message.pretty_print()

    print("\n========== Debug Info ==========")
    print("task_type:", result.get("task_type"))
    print("llm_calls:", result.get("llm_calls"))


def stream_agent_updates(user_input: str, thread_id: str = "demo-thread"):
    """
    用 updates 模式流式查看每个节点的输出。
    适合调试 LangGraph 执行过程。
    """
    initial_state = {
        "messages": [HumanMessage(content=user_input)],
        "user_input": user_input,
        "task_type": "",
        "error": None,
        "llm_calls": 0,
    }

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    print("\n========== Streaming Updates ==========\n")

    for chunk in graph.stream(
        initial_state,
        config=config,
        stream_mode="updates",
    ):
        print(chunk)


def stream_agent_messages(user_input: str, thread_id: str = "demo-thread"):
    """
    用 messages 模式流式输出模型 token。
    适合做命令行聊天或前端 SSE。
    """
    initial_state = {
        "messages": [HumanMessage(content=user_input)],
        "user_input": user_input,
        "task_type": "",
        "error": None,
        "llm_calls": 0,
    }

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    print("\n========== Streaming Messages ==========\n")

    for message_chunk, metadata in graph.stream(
        initial_state,
        config=config,
        stream_mode="messages",
    ):
        if message_chunk.content:
            print(message_chunk.content, end="", flush=True)

    print()


# =========================
# 9. 测试入口
# =========================

if __name__ == "__main__":
    # 测试 1：普通问答
    run_agent(
        "请用通俗的话解释 LangGraph 为什么适合构建 Agent。",
        thread_id="user-001",
    )

    # 测试 2：工具调用
    run_agent(
        "帮我计算 12849 / 6 等于多少",
        thread_id="user-001",
    )

    # 测试 3：本地知识查询
    run_agent(
        "bind_tools 有什么作用？",
        thread_id="user-001",
    )

    # 测试 4：学习计划生成
    run_agent(
        "帮我制定一个 5 天 LangGraph 学习计划",
        thread_id="user-001",
    )

    # 测试 5：调试流式输出
    stream_agent_updates(
        "帮我计算 3 + 4 * 10",
        thread_id="user-002",
    )

    # 测试 6：token 流式输出
    stream_agent_messages(
        "请用三句话解释 StateGraph、Node、Edge 的关系。",
        thread_id="user-003",
    )