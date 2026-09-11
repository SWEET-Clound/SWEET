import sqlite3
from typing import Literal
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END


class SWEET(TypedDict):
    question: str
    intent: str
    table_name: str
    sql: str
    sql_valid: bool
    rows: list
    answer: str
    error: str

def init_db():
    conn = sqlite3.connect("demo.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT,
        amount INTEGER
    )
    """)

    cursor.execute("DELETE FROM sales")

    cursor.executemany(
         "INSERT INTO sales (month, amount) VALUES (?, ?)",
        [
            ("2025-01", 100),
            ("2025-02", 150),
            ("2025-03", 130),
            ("2025-04", 180),
            ("2025-05", 210),
            ("2025-06", 240),
        ]
    )

    conn.commit()
    conn.close()

def classify_intent(state: SWEET):
    question = state["question"]

    if "销售" in question or "数据" in question or "查询" in question:
        return {"intent": "data_query"}

    return {
        "intent": "unknown",
        "answer": "我只能处理销售数据查询问题。"
    }

def route_after_intent(state: SWEET) -> Literal["select_table", "end_node"]:
    if state["intent"] == "data_query":
        return "select_table"
    return "end_node"

def select_table(state: SWEET):
    return {"table_name": "sales"}

def generate_sql(state: SWEET):
    question = state["question"]

    if "总" in question or "总额" in question:
        sql = "SELECT SUM(amount) AS total_amount FROM sales"

    elif "每月" in question or "趋势" in question:
        sql = "SELECT month, amount FROM sales ORDER BY month"

    else:
        sql = "SELECT month, amount FROM sales ORDER BY month"

    return {"sql": sql}

def validate_sql(state: SWEET):
    sql = state["sql"].strip().lower()

    forbidden_words = ["insert", "update", "delete", "drop", "alter", "truncate"]

    if not sql.startswith("select"):
        return {
            "sql_valid": False,
            "error": "只允许 SELECT 查询。"
        }

    for word in forbidden_words:
        if word in sql:
            return {
                "sql_valid": False,
                "error": f"SQL 包含禁止操作：{word}"
            }

    return {"sql_valid": True}

def route_after_validation(state: SWEET) -> Literal["execute_sql", "error_node"]:
    if state["sql_valid"]:
        return "execute_sql"
    return "error_node"


def execute_sql(state: SWEET):
    try:
        conn = sqlite3.connect("demo.db")
        cursor = conn.cursor()

        cursor.execute(state["sql"])
        rows = cursor.fetchall()

        conn.close()

        return {"rows": rows}

    except Exception as e:
        return {
            "error": str(e),
            "rows": []
        }


def analyze_result(state: SWEET):
    rows = state["rows"]

    if not rows:
        return {"answer": "没有查询到数据。"}

    if "sum" in state["sql"].lower():
        total = rows[0][0]
        return {"answer": f"销售总额是 {total}。"}

    return {"answer": f"查询到 {len(rows)} 条数据：{rows}"}


def error_node(state: SWEET):
    return {"answer": f"SQL 校验失败：{state['error']}"}


def end_node(state: SWEET):
    return state


init_db()

builder = StateGraph(SWEET)

builder.add_node("classify_intent", classify_intent)
builder.add_node("select_table", select_table)
builder.add_node("generate_sql", generate_sql)
builder.add_node("validate_sql", validate_sql)
builder.add_node("execute_sql", execute_sql)
builder.add_node("analyze_result", analyze_result)
builder.add_node("error_node", error_node)
builder.add_node("end_node", end_node)

builder.add_edge(START, "classify_intent")

builder.add_conditional_edges(
    "classify_intent",
    route_after_intent,
    ["select_table", "end_node"]
)

builder.add_edge("select_table", "generate_sql")
builder.add_edge("generate_sql", "validate_sql")

builder.add_conditional_edges(
    "validate_sql",
    route_after_validation,
    ["execute_sql", "error_node"]
)

builder.add_edge("execute_sql", "analyze_result")
builder.add_edge("analyze_result", END)
builder.add_edge("error_node", END)
builder.add_edge("end_node", END)

graph = builder.compile()

result = graph.invoke({
    "question": "查询每月销售数据",
    "intent": "",
    "table_name": "",
    "sql": "",
    "sql_valid": False,
    "rows": [],
    "answer": "",
    "error": ""
})

print("SQL:", result["sql"])
print("Rows:", result["rows"])
print("Answer:", result["answer"])