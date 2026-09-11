# 带短期记忆的agent 基于langgraph
import os
from http.client import responses

from langgraph.graph import MessagesState, START, END, StateGraph
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver

api_key = os.environ.get("DEEPSEEK_API_KEY")\

model = init_chat_model(
    "deepseek-v4-pro",
    api_key = api_key,
    temperature = 0,
)

def llm_node(state: MessagesState):
    responses = model.invoke(state["messages"])
    return {"messages": responses}

builder = StateGraph(MessagesState)

builder.add_node("llm_node",llm_node)

builder.add_edge(START, "llm_node")
builder.add_edge("llm_node", END)

memory = InMemorySaver()

# 将记忆挂载到状态图上
graph = builder.compile(checkpointer=memory)

config = {
    "configurable": {
        "thread_id": "user_001"
    }
}
config1 = {
    "configurable": {
        "thread_id": "user_002"
    }
}

result_1 = graph.invoke(
    {
        "messages": [
            {"role": "user", "content": "我叫小明。"}
        ]
    },
    config=config
)

result_3 = graph.invoke(
    {
        "messages": [
            {"role": "user", "content": "我叫小wang。"}
        ]
    },
    config=config1
)

result_2 = graph.invoke(
    {
        "messages": [
            {"role": "user", "content": "我叫什么？"}
        ]
    },
    config=config1
)

for message in result_2["messages"]:
    message.pretty_print()
