from langchain.tools import tool
from langchain.chat_models import init_chat_model
from langchain_core.messages import ToolMessage
import os

# 模型定义
api_key = os.getenv('DEEPSEEK_API_KEY')
model = init_chat_model(
    "deepseek-v4-pro",
    api_key=api_key,
    temperature = 0
)

# 定义工具 @tool
@tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b

@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b

@tool
def divide(a: int, b: int) -> float:
    """Divide two integers."""
    return a / b

# 工具列表
tools = [add, divide, multiply]
# 字典 方便根据工具名找工具
tools_by_name = {tool.name: tool for tool in tools}
# 将工具绑定到模型上 让模型知道有哪些工具可以调用
model_with_tools = model.bind_tools(tools)

# 图的状态主要用来储存信息和大模型调用次数
from langchain.messages import AnyMessage
from typing_extensions import TypedDict, Annotated
import operator

class MessageState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    llm_calls: int


# Define model node
from langchain.messages import SystemMessage

def llm_call(state: dict):
    """调用大模型判断是否call工具"""
    return {
        "messages": [
            model_with_tools.invoke([
                SystemMessage(
                    content = "你是一个计算助手，通过输入的一组数字进行相关计算"
                )
            ]
            + state["messages"]
            )
        ],
        "llm_calls": state.get('llm_calls', 0) + 1
    }

# Define tool node
from langchain.messages import SystemMessage
# 读取上一条AImessages中的tool_call 并执行对应工具 将返回结果包装成ToolMessages返回
def tool_node(state: dict):
    """tool call"""
    result = []
    # 取message中的最后一条消息 因为最后一条包含toolcall
    for tool_call in state['messages'][-1].tool_calls:
        tool = tools_by_name[tool_call['name']]
        # 工具执行
        observation = tool.invoke(tool_call["args"])
        result.append(ToolMessage(content=observation, tool_call_id=tool_call["id"]))
    return {"messages": result}


#Define end logic
from typing import Literal
from langgraph.graph import StateGraph, START, END, MessagesState


def should_continue(state: MessagesState) -> Literal["tool_node", END]:
    """Decide if we should continue the loop or stop based upon whether the LLM made a tool call"""

    messages = state["messages"]
    last_message = messages[-1]

    # If the LLM makes a tool call, then perform an action
    if last_message.tool_calls:
        return "tool_node"

    # Otherwise, we stop (reply to the user)
    return END

# Build and compile the agent
# Build workflow
agent_builder = StateGraph(MessagesState)

# Add nodes
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_node("tool_node", tool_node)

# Add edges to connect nodes
# 执行完START一定去llm_call
agent_builder.add_edge(START, "llm_call")
# 执行完llm_call 走should_continue should_continue返回tool_node就走tool_node 返回END就走END
agent_builder.add_conditional_edges(
    "llm_call",
    should_continue,
    ["tool_node", END]
)
agent_builder.add_edge("tool_node", "llm_call")

# Compile the agent
agent = agent_builder.compile()

# Show the agent
from IPython.display import Image, display
display(Image(agent.get_graph(xray=True).draw_mermaid_png()))

# Invoke
from langchain.messages import HumanMessage
messages = [HumanMessage(content="Add 3 and 4.")]
messages = agent.invoke({"messages": messages})
for m in messages["messages"]:
    m.pretty_print()