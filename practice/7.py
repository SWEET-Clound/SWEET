from typing import Literal
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import InMemorySaver

import sqlite3

class SWEET(TypedDict):
    user_input: str
    intent: str
    answer: str

def classify_intent(state: SWEET):
    text = state["user_input"]

    if "SQL" in text or "查询" in text or "数据" in text:
        return {"intent": "data_query"}

    if "你好" in text or "hello" in text.lower():
        return {"intent": "chat"}

    return {"intent": "unknown"}

def data_query(state: SWEET):
    return {"answer": "这是一个数据查询问题，下一步生成sql"}

def chat_node(state: SWEET):
    return {"answer": "你好，我可以帮你搭建 Agent。"}

def unknown_node(state: SWEET):
    return {"answer": "我暂时不确定你的意图，需要你补充说明。"}

def route_by_intent(state: SWEET) -> Literal["data_query", "chat_node", "unknown_node"]:
    if state["intent"] == "data_query":
        return "data_query"

    if state["intent"] == "chat":
        return "chat_node"

    return "unknown_node"

builder = StateGraph(SWEET)

builder.add_node("classify_intent", classify_intent)
builder.add_node("data_query", data_query)
builder.add_node("chat_node", chat_node)
builder.add_node("unknown_node", unknown_node)

builder.add_edge(START, "classify_intent")

builder.add_conditional_edges(
    "classify_intent",
    route_by_intent,
    ["data_query", "chat_node", "unknown_node"]
)

builder.add_edge("data_query", END)
builder.add_edge("chat_node", END)
builder.add_edge("unknown_node", END)

graph = builder.compile()

print(graph.invoke({
    "user_input": "帮我查询 2025 年销售数据",
    "intent": "",
    "answer": ""
}))

print(graph.invoke({
    "user_input": "你好",
    "intent": "",
    "answer": ""
}))