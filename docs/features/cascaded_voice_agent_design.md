# 第一版级联语音 Agent 技术设计

> 状态：P0～P3、P8 本地门禁已完成，P4～P7 代码完成但真实服务验收待补  
> 日期：2026-10-13  
> 面向范围：手机 App 前台实时语音对话  
> 编排方式：自研异步 Frame/Processor 流水线，Pipecat 仅作源码参考

## 1. 决策结论

第一版只实现同步级联主链路：

```text
手机 App
  │ WebRTC 上行音频
  ▼
aiortc Transport
  ▼
Energy VAD
  ▼
阿里云流式 ASR
  ▼
现有 qll_chat /chat SSE
  ▼
VoiceAgent（直接继承 BaseToolAgent）
  └─ search_weisou + read_weisou_document（首版配套工具）
  ▼
中文文本分片
  ▼
CosyVoice 流式 TTS
  ▼
WebRTC 下行音轨
```

中间 LLM 不使用 Pipecat 自带的 `QwenLLMService`。仓库新增独立 `VoiceAgent` 作为语音 LLM 层；它直接继承 `BaseToolAgent`，不继承 `GenericAgent`。语音识别得到 final transcript 后仍调用现有 `/chat` SSE 接口，由 `functionName=voiceAgent` 路由到 `VoiceAgent`，复用现有模型调用、MCP 执行、SSE 和日志协议。

首版 `VoiceAgent` 只开放服务端只读的微搜配套工具 `search_weisou` 和 `read_weisou_document`。知识库、联网搜索、本地工具、Skill、客户端工具和写操作均不开放。后续能力必须逐项增加并单独验证，不能从 GenericAgent 全量继承。

白名单由轻量 `tool_policy.py` 同时过滤模型可见 schema 和服务端执行注册表；工具只有名称映射但没有可执行注册项时也拒绝，避免向模型暴露无法实际执行的伪工具。

多智能体异步委派不属于第一版。未来可以在现有 Agent 内部演进，或在 `ExistingAgentProcessor` 旁增加异步任务分支，但当前代码不维护第二套任务运行时。

## 2. 为什么选择自研编排

本地 Pipecat 源码位于：

```text
D:/APP date/python/pipecat-main/pipecat-main
```

重点参考了三个实现：

| Pipecat 源码 | 借鉴内容 | 本项目实现 |
|---|---|---|
| `src/pipecat/processors/frame_processor.py` | 每处理器独立队列、SystemFrame 优先 | `processor.py` |
| `src/pipecat/pipeline/pipeline.py` | 线性 Processor 装配和生命周期 | `pipeline.py` |
| `src/pipecat/transports/smallwebrtc/transport.py` | PCM 重采样、10 ms 输出节奏 | `transport.py` |

当前本地 Pipecat 已将顶层运行模型演进为 `PipelineWorker + WorkerRunner`，并把 `PipelineTask` 标为兼容别名。第一版没有照搬 Worker、Bus、Jobs 和跨 Worker RPC：这些能力服务于多运行单元和多智能体协同，属于 P11，而不是单会话级联链路的必要条件。

没有复制 Pipecat 的完整框架。首版只保留当前业务必需的能力：

- 单向下游 Frame 流。
- 控制 Frame 抢占普通数据。
- 每个 Processor 独立异步任务。
- Start/Stop 传播和有界关闭。
- Agent 和 TTS 后台任务取消。
- 一对一 WebRTC 音频。

暂不实现上游 Frame、并行 Pipeline、指标观察器、插件系统、跨 Pipeline Bus 和分布式 Worker。

## 3. 团队职责

| 范围 | 负责人 | 当前仓库 |
|---|---|---|
| 麦克风、扬声器、WebRTC PeerConnection、前后台切换 | 手机端 | 只定义协议 |
| App 登录态、可信身份、Agent 请求模板、TURN 凭据 | 业务后端 | 只定义协议 |
| 语音短期令牌、WebRTC 信令、级联编排 | 算法服务 | 骨架完成，分阶段验收 |
| VAD、ASR、VoiceAgent SSE、TTS、打断 | 算法服务 | 分阶段验收 |
| VoiceAgent、微搜工具、MCP 和权限 | qll_chat | 首版只开放微搜查询与正文读取 |
| 多智能体异步任务 | 后续升级 | 首版不实现 |

## 4. 算法内部架构

```text
services/cascaded_voice_agent_service.py
  ├─ VoiceSessionRegistry
  │    └─ 短期 voice_token + TrustedUserContext
  ├─ WebRTCMediaSession
  │    ├─ aiortc RTCPeerConnection
  │    ├─ 输入 AudioResampler → 16 kHz mono PCM
  │    ├─ RawPCMOutputTrack ← 24 kHz mono PCM
  │    └─ DataChannel 状态事件
  └─ CascadedVoiceSession
       └─ VoicePipeline
            ├─ EnergyVADProcessor
            ├─ AliyunStreamingSTTProcessor
            ├─ ExistingAgentProcessor
            ├─ StreamingTTSProcessor
            └─ CallbackProcessor
```

每个 Processor 有自己的 `asyncio.PriorityQueue`。`InterruptFrame`、`StopFrame`、错误等 SystemFrame 的优先级高于音频、文本等普通 Frame，因此用户插话不需要等待 Agent token 或文本队列处理完。话轮开始/结束 Frame 保持普通队列顺序，避免越过已经排队的 PCM，导致 ASR 提前 start/stop 和句尾丢音。

## 5. Frame 契约

| Frame | 生产者 | 消费者 | 含义 |
|---|---|---|---|
| `InputAudioFrame` | WebRTC | VAD、ASR | 16 kHz mono PCM |
| `InterruptFrame` | VAD | Agent、TTS、输出 | 新话轮开始，取消旧输出 |
| `UserSpeechStartedFrame` | VAD | ASR/状态 | 建立新 `turn_id` |
| `UserSpeechStoppedFrame` | VAD | ASR/状态 | 服务端检测到静音 |
| `TranscriptFrame` | ASR | ExistingAgent | partial 或 final 文本 |
| `AgentTextFrame` | ExistingAgent | TTS | `status=0 token` 或最终答案 |
| `AgentEndFrame` | ExistingAgent | TTS/输出 | 唯一最终答案及引用 |
| `OutputAudioFrame` | TTS | WebRTC | 24 kHz mono PCM |
| `StateFrame` | 各环节 | DataChannel | UI 状态事件 |
| `ErrorFrame` | 各环节 | DataChannel/会话 | 归一化错误 |

## 6. 连续对话与打断

### 6.1 新话轮

1. WebRTC 音频持续进入 VAD；ASR 在未开始话轮时只保留最近 300 ms 前导音频。
2. 连续高能量达到 `vad_start_ms` 后，VAD 递增 `turn_id`。
3. VAD 先发 `InterruptFrame`，再发 `UserSpeechStartedFrame`。
4. `ExistingAgentProcessor` 取消上一轮 HTTP SSE 读取任务。
5. `StreamingTTSProcessor` 取消 CosyVoice 任务。
6. WebRTC 输出轨清空尚未播放的 PCM。

### 6.2 ASR final

每个 VAD 话轮创建一个新的阿里云 Recognition 实例，遵循 `start → send_audio_frame → stop` 生命周期，不复用已经 stop 的实例。话轮开始后先补发最近 300 ms 前导 PCM，再按约 100 ms 分包发送；话轮结束时 `stop` 会等待服务端处理完剩余音频。

Recognition 回调运行在 SDK 工作线程。适配器用 `loop.call_soon_threadsafe` 将中间转写送回主事件循环，在 `stop` 完成后汇总该话轮文本，并且只生成一个 `is_final=true` 的 `TranscriptFrame`。只有非空 final 会启动现有 Agent 请求；SDK 错误统一转换为带 `turn_id` 的非致命 `ErrorFrame`。

### 6.3 Agent 流

`ExistingAgentSSEClient` 深拷贝业务后端提供的可信请求模板，只覆盖：

- `question`
- `questionId`
- `answerId`
- `history`
- `functionName=voiceAgent`
- 服务端可信的 `tenantId/userId`

可信模板固定使用 `functionName=voiceAgent`，由 `/chat` 路由到独立 `VoiceAgent`。VoiceAgent 直接复用 `BaseToolAgent` 的模型循环和 SSE 协议，并在工具发现后再次执行白名单过滤，确保首版只有 `search_weisou` 和 `read_weisou_document` 可见和可执行。

SSE 状态处理：

| status | 处理 |
|---:|---|
| 0 | token 送入 TTS 文本分片 |
| 1 | 保存最终答案、引用和会话 history |
| 2 | 内部推理内容忽略；`assistant.thinking` 由请求启动事件表达 |
| 3/4 | 通过 DataChannel 告知前端正在调用工具，不播报内部内容 |
| 11 | 第一版返回错误；需要客户端续跑的工具暂不支持 |
| 100 | 保活事件忽略 |

服务端内部工具调用可以完整执行，因为 `/chat` 会持续输出 3/4/0/1。需要手机 UI 回填的 clientTool 不属于第一版。

## 7. TTS 策略

- `SentenceChunker` 优先按 `。！？；，!?;,` 切片。
- 文本达到 `tts_chunk_max_chars` 时强制提交，避免长句等待。
- TTS 在独立 asyncio 任务中执行，Processor 主队列仍可处理 `InterruptFrame`。
- `InterruptFrame.turn_id` 形成单调话轮门槛，已经排队但属于旧话轮的文本和结束帧不会重新启动 TTS。
- SDK 音频回调只接受当前 `turn_id`，迟到音频被丢弃。
- `streaming_complete()` 的等待超时由 asyncio 控制；可中断合成要求 DashScope SDK 不低于 1.26.4。
- WebRTC 输出轨按 10 ms 节奏发送 24 kHz mono PCM；没有待播音频时发送静音帧保持音轨连续。

## 8. HTTP 与 WebRTC 协议

### 8.1 业务后端创建可信会话

```http
POST /voice/sessions
X-Internal-Service-Token: <service-secret>
Content-Type: application/json
```

```json
{
  "session_id": "可选，留空则服务端生成",
  "tenant_id": "100002",
  "user_id": "732849986415722499",
  "agent_request": {
    "tenantId": "100002",
    "userId": "732849986415722499",
    "channel": "writer",
    "functionName": "voiceAgent",
    "history": [],
    "algExtra": {
      "sceneMcp": {"enable": 1},
      "searchRange": {
        "innerCorpus": false,
        "network": false,
        "weisou": true,
        "db": false
      }
    }
  }
}
```

返回短期单会话令牌：

```json
{
  "session_id": "...",
  "voice_token": "...",
  "expires_at": 1791796228.0,
  "signaling_url": "/voice/sessions/.../offer"
}
```

`agent_request` 只能由可信业务后端创建，不能直接接受手机端提交。

首版继续使用现有 `writer` 模型配置通道，避免额外改造模型配置服务；Agent 的独立路由只由 `functionName=voiceAgent` 标识。后续若业务后端和模型配置服务正式支持 `voice` 通道，再单独迁移。

### 8.2 手机交换 SDP

```http
POST /voice/sessions/{session_id}/offer
Authorization: Bearer <voice_token>
Content-Type: application/json
```

```json
{
  "sdp": "...",
  "type": "offer"
}
```

返回 SDP answer。`voice_token` 首次 offer 后标记为已连接，不能建立第二条 PeerConnection。STUN/TURN 地址和凭证只由语音服务端通过 `VOICE_WEBRTC_ICE_URLS`、`VOICE_WEBRTC_ICE_USERNAME`、`VOICE_WEBRTC_ICE_CREDENTIAL` 配置，手机端提交 `ice_servers` 会被拒绝。

### 8.3 DataChannel 事件

```text
session.ready
assistant.listening
assistant.thinking
assistant.tool
assistant.speaking
assistant.interrupted
assistant.answer.completed
session.error
session.closed
```

首版不要求字幕和结果卡片。引用只随 `assistant.answer.completed` 返回，前端可暂时忽略。该事件只代表 Agent 文本终态，不代表 TTS 播放完成；带当前 `turn_id` 的后续 `assistant.listening` 才代表本轮 TTS 已结束。首次不带 `turn_id` 的 `assistant.listening` 仅表示会话准备完成。

## 9. 安全边界

- 手机端永远不持有 DashScope API Key、内部服务凭证或完整 Agent 权限模板。
- `X-Internal-Service-Token` 只允许业务后端调用创建会话接口。
- `voice_token` 使用安全随机数、短期有效并绑定单个 `session_id`。
- ICE/TURN 服务器由服务端配置，不能通过手机 SDP 交换接口覆盖。
- Agent 请求中的 tenant/user 必须与可信上下文一致。
- 日志不得输出原始音频、完整 Agent 模板、模型密钥和 voice token。
- VoiceAgent 首版只允许 `search_weisou` 和 `read_weisou_document`，不接受请求侧扩大工具权限。

## 10. 故障与降级

| 故障 | 行为 |
|---|---|
| WebRTC offer 不合法 | 400 并关闭已创建资源 |
| voice token 无效 | 401 |
| ASR/TTS 初始化失败 | DataChannel `session.error`，fatal 时关闭会话 |
| Agent HTTP 非 200/超时 | 播报固定失败提示，不伪造业务答案 |
| Agent 无 status=1 且无 token | 视为失败 |
| clientTool status=11 | 明确提示首版不支持 |
| 用户插话 | 取消旧 Agent/TTS 并清空服务端音频缓冲 |
| WebRTC 断开 | 幂等关闭整条流水线 |

## 11. 前端和业务后端规划

### 手机端

- 建立带音频发送和接收方向的 `RTCPeerConnection`。
- 创建名为 `voice-events` 的 DataChannel。
- 上行音频使用系统 WebRTC Opus 编码，不自行转 PCM。
- 播放服务器音轨；收到 `assistant.interrupted` 后同步清理本地额外播放器缓冲。
- 前后台、来电或网络切换时，首版结束并重新建立会话。

### 业务后端

- 根据 App 登录态构造 tenant/user 和完整 `agent_request`。
- 生成 TURN 短期凭据。
- 服务间调用 `/voice/sessions`，只把 voice token 和信令地址返回手机。
- 不代理实时音频，不把 DashScope Key 下发手机。

## 12. 后续多智能体升级

第一版稳定后，如果确实需要“查询运行时仍可继续闲聊”，再增加：

```text
VoiceAgent / ExistingAgentProcessor
  ├─ 普通同步 VoiceAgent turn
  └─ AsyncTaskProcessor（后续）
       └─ 受信任子智能体注册表
```

升级时保留 Frame、Pipeline、WebRTC、ASR 和 TTS；只新增任务 Frame 和旁路 Processor，不修改手机媒体协议。异步任务必须重新设计任务所有权、取消、唯一终态、结果安全话轮和写操作审批，不能复用首版同步请求假装异步。

## 13. 当前限制

- 能量 VAD 用于首版打通和采集真实数据，嘈杂环境需要后续换成 Silero 或调节阈值。
- 仅支持 16 kHz mono ASR 输入和 24 kHz mono TTS 输出。
- 首版每次只运行一个 Agent turn；用户插话会取消旧请求。
- 不支持需要前端回填后续请求的 `status=11` clientTool。
- 会话注册表在进程内，不支持多实例共享和服务重启恢复。
- 没有自动供应商切换和音频持久化。

## 14. 代码入口与实施状态

第一版完整代码位于 `qll_chat/cascaded_voice_agent/`、`qll_chat/agents/voice_agent.py`、`qll_chat/chat.py` 的 `voiceAgent` 路由和 `services/cascaded_voice_agent_service.py`。具体搭建顺序、全部源码索引、配置和运行命令见 `cascaded_voice_agent_build_guide.md`。

具体阶段、验收门禁和当前状态以 `cascaded_voice_agent_project_plan.md` 为唯一依据：

```text
qll_chat/cascaded_voice_agent/
├─ frames.py
├─ processor.py
├─ pipeline.py
├─ vad.py
├─ existing_agent.py
├─ tts_chunker.py
├─ tts.py
├─ transport.py
├─ session_registry.py
├─ session.py
├─ settings.py
└─ providers/
   ├─ aliyun_stt.py
   └─ cosyvoice_tts.py

services/cascaded_voice_agent_service.py
resources/application/cascaded_voice_agent/configs/default.conf
resources/application/cascaded_voice_agent/configs/dev.conf
```

当前 P0～P3 和 P8 已通过本地代码门禁，P4～P7 已完成代码实现但尚未通过真实 `/chat`、微搜、ASR 和 TTS 门禁。下一步必须先重建 Python 3.12.11 环境，再完成 P4～P7 真实联调；这些门禁通过前不得进入 P9 手机试用或 P11 多智能体升级。
