# Week 4 SGLang Lab：OpenAI API contract 到 server 入口

**时间：核心 2h。先读取 Week 3 固定 SHA。**

1. 用该 SHA 对应的 docs（网页只能作导航，最终记入本地 SHA/路径）查 OpenAI-compatible completion/chat API 的请求字段、流式响应和错误含义。
2. 写 `sglang_notes/w04_api_contract_compare.md`，包含两张表：
   - 本地 `POST /v1/agent/responses` 与 SGLang API：共同字段、仅本地字段、不可直接类比的字段。
   - request ID、stream、模型/鉴权：本地是否实现、为什么。
3. 在 clone 内用 `rg -n "request_id|OpenAI|chat/completions" python tests` 找到实际入口候选（最多 5 个符号），画“HTTP → validation → request object”的高层图。不能因命名猜测而声称调用关系。
4. 尝试对一个相关纯 API/unit 测试执行 `pytest --collect-only -q`；保存 PATH、SHA、输出。

**验收**：比较表 + 至少一个真实文件/符号 + collect/run 证据。**边界**：不启动大模型 server，不收集真实用户 prompt。
