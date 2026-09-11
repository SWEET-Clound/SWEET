import os
from http.client import responses

from langgraph.graph import MessagesState, START, END, StateGraph
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langchain_core.messages import ToolMessage
from typing import Literal

api_key = os.environ.get("DEEPSEEK_API_KEY")
model = init_chat_model(
    "deepseek-v4-pro",
    api_key=api_key,
    temperature = 0
)

@tool
def add(a: int, b: int) -> int:
    """将两个数字相加"""
    return a + b

@tool
def multiply(a: int, b: int) -> int:
    """将两个数字相乘"""
    return a * b

@tool
def divide(a: float, b: float) -> float:
    """将两个数字相除"""
    return a / b

# 工具列表
tools = [add, multiply, divide]
# 工具名称 方便模型根据名称找工具
tools_by_name = {tool.name: tool for tool in tools}
# 将工具绑定到模型上 让模型知道有哪些工具可以调用
model_with_tools = model.bind_tools(tools)

def llm_call (state: MessagesState):
    # 将工具注入上下文
    responses = model_with_tools.invoke(state["messages"])
    return {"messages": [responses]}

def tool_node(state: MessagesState):
    last_message = state["messages"][-1]
    tool_messages = []

    for tool_call in last_message.tool_calls:
        tool = tools_by_name[tool_call["name"]]
        print(tool_call)
        result = tool.invoke(tool_call["args"])

        tool_messages.append(
            # 将工具id和结果写入ToolMessage
            ToolMessage(
                content = str(result),
                tool_call_id = tool_call["id"],
            )
        )
    return {"messages": tool_messages}

def should_continue(state: MessagesState) -> Literal["tool_node", "end"]:
    last_message = state["messages"][-1]

    if last_message.tool_calls:
        return "tool_node"

    return END

# MessagesState 是各节点共享状态数据包
builder = StateGraph(MessagesState)

builder.add_node("tool_node",tool_node)
builder.add_node("llm_call",llm_call)

builder.add_edge(START,"llm_call")

builder.add_conditional_edges(
    "llm_call",
    should_continue,
    ["tool_node",END]
)

builder.add_edge("tool_node", "llm_call")

graph = builder.compile()

# result = graph.invoke({
#     "messages": [
#         {"role": "user", "content": "请计算3加4再乘以10"}
#     ]
# })
#
# for message in result["messages"]:
#     message.pretty_print()

# for chunk in graph.stream(
#     {
#         "messages": [
#             {"role": "user", "content": "请计算 8 乘以 9"}
#         ]
#     },
#     stream_mode="updates"
# ):
#     print(chunk)

for message_chunk, metadata in graph.stream(
    {
        "messages": [
            {"role": "user", "content": "请用三句话介绍 LangGraph"}
        ]
    },
    stream_mode="messages"
):
    if message_chunk.content:
        print(message_chunk.content, end="", flush=True)