# 级联语音 Agent · 调用时序图

> 依据代码事实源：`qll_chat/cascaded_voice_agent/` 各模块 + `services/cascaded_voice_agent_service.py`。
> 参与者即真实类名，箭头即真实方法/帧。

---

## 一、会话建立

```mermaid
sequenceDiagram
    autonumber
    participant B as 业务后端
    participant S as 服务入口<br/>cascaded_voice_agent_service
    participant R as VoiceSessionRegistry
    participant SESS as CascadedVoiceSession
    participant PL as VoicePipeline
    participant T as WebRTCMediaSession

    B->>S: POST /voice/sessions (X-Internal-Service-Token + agent_request)
    S->>R: create(TrustedUserContext)
    R-->>S: session_id + voice_token(短期随机)
    S-->>B: {session_id, voice_token, signaling_url}

    Note over B,T: 手机拿到 voice_token 后交换 SDP
    B->>S: POST /voice/sessions/{id}/offer (Bearer voice_token + SDP offer)
    S->>R: authenticate(session_id, token, mark_connected=true)
    R-->>S: SessionRegistration(单次连接占用)
    S->>T: accept_offer(sdp, "offer")
    T-->>S: SDP answer

    S->>SESS: run(media, TrustedUserContext)
    SESS->>SESS: _create_processors()<br/>VAD→ASR→Agent→TTS
    SESS->>PL: start()
    PL->>PL: 反向启动各 Processor<br/>入队 StartFrame
    SESS->>T: send_event(session.ready)
    SESS->>T: send_event(assistant.listening)
```

---

## 二、完整话轮（用户说一句 → 播报回答）

```mermaid
sequenceDiagram
    autonumber
    participant App as 手机 App
    participant T as WebRTCMediaSession
    participant SESS as CascadedVoiceSession
    participant VAD as EnergyVADProcessor
    participant ASR as AliyunStreamingSTTProcessor
    participant AG as ExistingAgentProcessor
    participant CHAT as /chat + VoiceAgent
    participant TTS as StreamingTTSProcessor
    participant CV as CosyVoiceTTSService

    Note over App,CV: ① 上行音频 + VAD 起音
    App->>T: WebRTC 上行音轨(Opus)
    T->>SESS: _read_audio 重采样 16kHz → _on_audio(pcm)
    SESS->>VAD: InputAudioFrame
    VAD->>VAD: audioop.rms 能量达标, turn_id++
    VAD->>ASR: InterruptFrame(turn_id)
    VAD->>ASR: UserSpeechStartedFrame(turn_id)
    VAD->>ASR: StateFrame(assistant.listening)

    Note over App,CV: ② ASR 话轮生命周期
    ASR->>ASR: _start_turn 新建 Recognition.start()
    ASR->>ASR: 补发 300ms 前导 pre_roll
    VAD->>ASR: InputAudioFrame（连续音频流）
    ASR->>ASR: _feed_audio → send_audio_frame（约100ms/包）

    Note over App,CV: ③ 停音 + ASR final
    VAD->>ASR: UserSpeechStoppedFrame(turn_id)
    ASR->>ASR: _finish_turn → Recognition.stop()
    ASR->>AG: TranscriptFrame(turn_id, text, is_final=true)

    Note over App,CV: ④ Agent（SSE + 工具调用）
    AG->>AG: _run_turn 异步任务启动
    AG->>T: StateFrame(assistant.thinking)
    AG->>CHAT: POST /chat (functionName=voiceAgent, question)
    CHAT->>CHAT: VoiceAgent 循环 → 需要时 search_weisou/read_weisou_document
    CHAT-->>AG: SSE status=3/4（工具运行中）
    AG->>T: StateFrame(assistant.tool)
    CHAT-->>AG: SSE status=0 / status=1.token（文本 token）
    AG->>TTS: AgentTextFrame(turn_id, text)

    Note over App,CV: ⑤ TTS 流式合成
    TTS->>TTS: SentenceChunker 按中文标点分句
    TTS->>CV: send_text(chunk) → streaming_call
    CV-->>TTS: on_data(PCM 24kHz mono)
    TTS->>SESS: OutputAudioFrame(turn_id, audio)
    SESS->>T: send_audio(audio)
    T->>App: WebRTC 下行音轨（播放）
    TTS->>T: StateFrame(assistant.speaking)【首音频】

    Note over App,CV: ⑥ 终态收尾
    CHAT-->>AG: SSE status=1（answer + reference_data）
    AG->>TTS: AgentEndFrame(turn_id, answer, references)
    TTS->>CV: finish() → streaming_complete
    TTS->>T: StateFrame(assistant.listening)【本轮 TTS 完成】
    AG->>T: StateFrame(assistant.answer.completed)
```

---

## 三、插话（打断）

```mermaid
sequenceDiagram
    autonumber
    participant VAD as EnergyVADProcessor
    participant AG as ExistingAgentProcessor
    participant TTS as StreamingTTSProcessor
    participant SESS as CascadedVoiceSession
    participant T as WebRTCMediaSession
    participant CV as CosyVoiceTTSService

    Note over VAD,CV: 用户再次开口 → VAD 生成更大的 turn_id
    VAD->>AG: InterruptFrame(turn_id=新)
    VAD->>TTS: InterruptFrame(turn_id=新)

    AG->>AG: 先 push_frame(Interrupt) 下传
    AG->>AG: _cancel_turn() 取消旧 HTTP SSE
    TTS->>TTS: _minimum_turn_id = 新 turn_id（门槛）
    TTS->>SESS: push_frame(Interrupt) → clear_audio()
    SESS->>T: clear_audio()（清空播放缓冲）
    SESS->>T: send_event(assistant.interrupted)
    TTS->>CV: cancel() → streaming_cancel（等服务端确认）

    Note over VAD,CV: 旧话轮的迟到数据被双重门槛丢弃
    AG->>AG: 旧 turn 的 AgentTextFrame 不再下传（HTTP 已取消）
    TTS->>TTS: turn_id < _minimum_turn_id 的帧直接丢弃
    CV->>CV: generation 不匹配的迟到回调直接丢弃
```

---

## 四、关闭与资源释放

```mermaid
sequenceDiagram
    autonumber
    participant X as 触发源(断线/空闲/显式/fatal)
    participant SESS as CascadedVoiceSession
    participant PL as VoicePipeline
    participant P as 各 FrameProcessor
    participant T as WebRTCMediaSession

    X->>SESS: close(reason)
    SESS->>SESS: _close_lock + 共享 _close_task（幂等）
    SESS->>PL: stop(reason)
    PL->>P: StopFrame（沿链传播）
    P->>P: on_stop() 清理（取消 Agent/TTS 任务、释放 SDK）
    PL-->>SESS: 各 Processor wait_closed（10s 超时强制取消）
    SESS->>T: send_event(session.closed)
    SESS->>T: close()（关闭 PeerConnection + 音轨）
    SESS->>SESS: state=closed, _closed_event.set()
```

---

## 五、Frame 流转全景（ASCII）

```
手机App ──Opus──▶ WebRTCMediaSession ──16kHz PCM──▶ _on_audio ──InputAudioFrame──▶
EnergyVAD ──InterruptFrame/UserSpeechStarted/UserSpeechStopped──▶
AliyunStreamingSTT ──TranscriptFrame(is_final)──▶
ExistingAgentProcessor ──AgentTextFrame/AgentEndFrame/StateFrame──▶
StreamingTTSProcessor ──OutputAudioFrame/StateFrame──▶
CallbackProcessor ──▶ CascadedVoiceSession._handle_output ──▶ WebRTCMediaSession ──▶ 手机

    (中断) EnergyVAD ──InterruptFrame(新turn_id)──▶ 全链清空 + 取消
    (错误) 任意 Processor ──ErrorFrame──▶ session.error / fatal时关闭
```

---

## 六、关键机制与代码对应

| 机制 | 代码位置 | 作用 |
|---|---|---|
| 单调 `turn_id` | `vad.py` 生成、全程携带 | 隔离旧/新话轮 |
| `generation` 序号 | `aliyun_stt.py`、`cosyvoice_tts.py` | 丢弃取消后迟到回调 |
| SystemFrame 优先 | `processor.py` `queue_frame`（优先级 0/10） | 打断永远插队 |
| 后台任务可取消 | `existing_agent.py` `_cancel_turn`、`tts.py` `_cancel_turn` | 打断即取消 HTTP/TTS |
| 幂等关闭 | `session.py` `_close_lock`+`_close_task`、`pipeline.py` `_stop_task` | 多处触发只清理一次 |
| fail-closed 白名单 | `tool_policy.py`、`settings.py`、`contracts.py` | 工具/身份/ICE 默认拒绝 |
