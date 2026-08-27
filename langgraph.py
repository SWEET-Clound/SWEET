from langgraph.graph import StateGraph, MessagesState, START, END, MessagesState


def mock_llm(state: MessagesState):
    # 模拟大模型 执行完后在对话信息添加ai消息
    return {"messages": [{"role": "ai", "content": "hello world"}]}

# 创建图
graph = StateGraph(MessagesState)
# 添加节点 加入图中
graph.add_node("mock_llm", mock_llm)
graph.add_edge(START, "mock_llm")
graph.add_edge("mock_llm", END)
graph = graph.compile()

graph.invoke({"messages": [{"role": "user", "content": "hi"}]})