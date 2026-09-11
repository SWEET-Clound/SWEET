from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from typing import Literal

class SWEET(TypedDict):
    score:int
    result:str

def check_score(state: SWEET):
    return state

def pass_node(state: SWEET):
    return {"result": "通过"}

def fail_node(state: SWEET):
    return {"result": "失败"}

def route_by_score(state: SWEET) -> Literal["pass_node", "fail_node"]:
    if state["score"] >= 60:
        return "pass_node"
    return "fail_node"

builder = StateGraph(SWEET)

builder.add_node("check_score", check_score)
builder.add_node("pass_node", pass_node)
builder.add_node("fail_node", fail_node)

builder.add_edge(START, "check_score")

builder.add_conditional_edges(
    "check_score",
    route_by_score,
    ["pass_node", "fail_node"]
)

builder.add_edge("pass_node", END)
builder.add_edge("fail_node", END)

graph = builder.compile()

print(graph.invoke({"score": 80, "result":""}))
print(graph.invoke({"score": 40, "result":""}))