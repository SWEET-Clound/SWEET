---
status: accepted
---

# 自研编排第一版级联语音链路

第一版使用自研异步 Frame/Processor 流水线承载 `WebRTC → VAD → ASR → 现有 qll_chat /chat → TTS → WebRTC`。Pipecat 仅作为优先级队列、Processor 生命周期和 SmallWebRTC 音频处理的源码参考，不作为运行时依赖。

ASR final 直接调用现有 `/chat` SSE 接口，并固定由 `functionName=voiceAgent` 路由到独立的 `VoiceAgent(BaseToolAgent)`。VoiceAgent 复用现有模型循环、MCP 执行、SSE、权限和日志协议，但首版只开放 `search_weisou` 与 `read_weisou_document`，不继承 GenericAgent 的完整能力。多智能体异步任务不进入第一版。

## Consequences

- 第一版只有一条同步 Agent turn；用户插话会取消旧 Agent 和 TTS。
- 服务端工具调用沿用现有 `/chat`，需要客户端续跑的 `status=11` 暂不支持。
- WebRTC 使用 aiortc，ASR/TTS 使用 DashScope SDK。
- 业务后端提供可信 Agent 请求模板；手机只获得短期 voice token。
- 多智能体异步任务保留为后续独立升级，不在首版预埋未验证运行时。
- 自研编排只实现首版必需能力，不复制 Pipecat 的完整框架。
