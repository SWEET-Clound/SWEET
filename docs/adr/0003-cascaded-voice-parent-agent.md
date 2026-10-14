---
status: superseded
---

# 级联语音 Agent 作为多智能体父智能体

> 已被 [ADR-0004](0004-self-hosted-cascaded-voice-orchestrator.md) 替代。首版不再直接使用 Pipecat，也不建设异步子智能体任务运行时。

级联方案使用 Pipecat 承载 `VAD → ASR → 文本 LLM → TTS` 实时流水线，文本 LLM 作为唯一的级联语音父智能体。父智能体通过共享的子智能体任务运行时直接委派受信任注册表中的子智能体，不再嵌套另一个父智能体；子任务立即返回 `accepted + task_id` 并在后台执行，完成结果在安全话轮注入原语音会话。首版注册表只开放只读 `SearchAgent`，不并发运行多个子任务，不允许模型或手机端提交可信身份与权限。

## Consequences

- Pipecat 负责实时媒体和事件流，不承担业务规划。
- 子智能体只返回结构化结果，不能直接向用户发声。
- 当前 `/multi_chat` 仍是 QA/OA 路由，不能作为子智能体任务运行时。
- 首版需要新增窄接口的 `AgentTaskGateway`；以后其他父智能体可以复用同一接口。
- 自研编排只形成替代设计，不与 Pipecat 首版同时实现。
