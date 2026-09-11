from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END

class State(TypedDict):
    number: int

def add_one(state: State):
    return {"number": state["number"] + 1}

def multiply_two(state: State):
    return {"number": state["number"] * 2}

def minus_two(state: State):
    return {"number": state["number"] - 2}

builder = StateGraph(State)

builder.add_node("add_one", add_one)
builder.add_node("multiply_two", multiply_two)
builder.add_node("minus_two", minus_two)

builder.add_edge(START, "add_one")
builder.add_edge("add_one", "multiply_two")
builder.add_edge("multiply_two", "minus_two")
builder.add_edge("minus_two",END)

graph = builder.compile()

result = graph.invoke({
    "number": 10
})

print(result)