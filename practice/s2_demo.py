"""S2 教学用纯函数模块 —— 完全不依赖 LLM / 网络 / API Key。

这些函数是 study_assistant.py 里「纯逻辑」的简化版，
目的是让你在没有网络、没有 DEEPSEEK_API_KEY 的条件下练习 pytest。

为什么单独建一个模块，而不是直接 import study_assistant.py？
因为 study_assistant.py 在 import 时就会执行 init_chat_model(...) 和
model.bind_tools(...)，产生真实模型副作用（这正是 S3 要解决的「测试接缝」问题）。
S2 先在这里练基本功，不碰那个副作用。
"""
import os


def divide(a, b):
    """普通除法。b 为 0 时抛出 ZeroDivisionError。"""
    return a / b


def calculator(expression: str) -> str:
    """与 study_assistant.calculator 同款的简单计算器（纯逻辑，无副作用）。

    - 正常算术：返回字符串结果，例如 "3 + 4 * 10" -> "43"
    - 非法字符：返回 Error 提示，不抛异常
    - 除零等运行时错误：返回 Calculation error 提示，不抛异常
    """
    allowed = set("0123456789+-*/(). ")
    if not set(expression) <= allowed:
        return "Error: expression contains unsupported characters."
    try:
        return str(eval(expression))
    except Exception as e:
        return f"Calculation error: {str(e)}"


def make_study_plan(topic: str, days: int = 3) -> str:
    """生成学习计划：每天一行，共 days 行。"""
    plan = []
    for day in range(1, days + 1):
        plan.append(f"Day {day}: 学习 {topic} 的第 {day} 个核心部分，完成一个小代码练习。")
    return "\n".join(plan)


def get_api_key() -> str | None:
    """从环境变量读取 API Key（模拟真实 Agent 里的 os.getenv("DEEPSEEK_API_KEY")）。"""
    return os.getenv("DEEPSEEK_API_KEY")


def ask_model(prompt: str) -> str:
    """模拟「调用外部大模型」这个不可控边界。

    真实场景里这里会发网络请求、花钱、可能超时。
    测试里绝不能真的调用它，应该用 mock 替换掉。
    所以这里故意抛异常：一旦测试真的触发了它，立刻失败并暴露问题。
    """
    raise RuntimeError("不要真的调用模型！测试里请用 mock 替换 ask_model。")


def summarize(prompt: str) -> str:
    """调用外部模型做总结。测试时把 ask_model mock 掉，只看 summarize 本身的行为。"""
    return ask_model(prompt)
