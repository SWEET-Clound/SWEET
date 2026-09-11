from langgraph.graph import  StateGraph, MessagesState, START, END

def mock_llm(state: MessagesState):
    return {
        "messages": [
            {"role": "ai", "content": "你好，你是模拟llm"}
        ]
    }

builder = StateGraph(MessagesState)

builder.add_node("mock_llm", mock_llm)

builder.add_edge(START, "mock_llm")
builder.add_edge("mock_llm", END)

graph = builder.compile()

result = graph.invoke({
    "messages": [
        {"role":"user", "content":"你好"}
    ]
})

print(result)