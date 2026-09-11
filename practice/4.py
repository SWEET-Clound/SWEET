import os
from http.client import responses

from langgraph.graph import MessagesState, START, END, StateGraph
from langchain.chat_models import init_chat_model

api_key = os.getenv("DEEPSEEK_API_KEY")

model = init_chat_model(
    "deepseek-v4-pro",
    api_key = api_key,
    temperature = 0
)

def llm_node(state: MessagesState):
    responses = model.invoke(state["messages"])
    return {"messages":[responses]}

builder = StateGraph(MessagesState)

builder.add_node("llm_node", llm_node)

builder.add_edge(START, "llm_node")
builder.add_edge("llm_node", END)

graph = builder.compile()

result = graph.invoke({
    "messages": [
        {"role":"user", "content":"用一句话解释langgraph是什么"}
    ]
})

for message in result["messages"]:
    message.pretty_print()

