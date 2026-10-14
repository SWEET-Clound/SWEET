# 第一版级联语音 Agent 搭建与完整代码指南

> 适用范围：手机 App 前台实时语音；算法服务自研编排；现有 `/chat` 负责 LLM、工具调用和微搜。
>
> 代码状态：P0～P3、P8 已通过本地门禁；P4～P7 代码完成，真实服务验收待补。

## 1. 最终目标和非目标

第一版只打通一条可持续对话、可插话、可调用只读查询工具的级联链路：

```text
手机 WebRTC
  → 16 kHz PCM
  → Energy VAD
  → 阿里云 Paraformer 流式 ASR
  → qll_chat /chat SSE
  → VoiceAgent(BaseToolAgent)
  → search_weisou / read_weisou_document
  → 增量文本
  → CosyVoice 流式 TTS
  → 24 kHz PCM
  → WebRTC/Opus
  → 手机播放
```

第一版明确不实现：

- 多智能体异步委派、后台任务恢复和跨会话任务总线。
- 写操作、审批、发送、删除和客户端续跑工具。
- Web 端、桌面端和复杂结果卡片。
- Redis 会话注册、集群迁移和服务重启恢复。
- 对 Pipecat Worker、Bus、Jobs、RTVI 和 Observers 的完整复制。

## 2. Pipecat 源码参考结论

本地参考源码：`D:/APP date/python/pipecat-main/pipecat-main`，许可证为 BSD-2-Clause。

| Pipecat 源码 | 借鉴原则 | 本项目代码 |
|---|---|---|
| `src/pipecat/frames/frames.py` | 音频、文本和控制信号统一为 Frame | `frames.py` |
| `src/pipecat/processors/frame_processor.py` | 独立队列、SystemFrame 优先、处理器串联 | `processor.py` |
| `src/pipecat/pipeline/pipeline.py` | 线性装配和统一生命周期 | `pipeline.py` |
| `src/pipecat/transports/smallwebrtc/transport.py` | aiortc、重采样、连续输出节奏 | `transport.py` |
| `src/pipecat/pipeline/worker.py` | 空闲关闭、取消和悬挂任务检查 | `session.py` |

本地 Pipecat 当前已经使用 `PipelineWorker + WorkerRunner`，`PipelineTask` 只是兼容别名。这套运行时还承担多 Worker、Bus、Jobs、指标和追踪；第一版只有一条固定语音链，因此没有照搬。自研外部 seam 只有三个方法：

```python
await pipeline.start()
await pipeline.queue_frame(frame)
await pipeline.stop(reason)
```

复杂性留在 `VoicePipeline` 和各 Processor 内部，调用方不需要理解队列、优先级、供应商回调线程或取消细节。

## 3. 完整代码交付清单

以下文件共同组成第一版完整代码。链接指向唯一事实源，均为真实实现，不是伪代码或方法占位；第 12 节同时提供当前完整源码快照，满足离线照文档搭建的需要。后续修复仍以链接所指源码为准。

### 3.1 LLM 与工具调用

| 文件 | 完整职责 |
|---|---|
| [`qll_chat/agents/voice_agent.py`](../../qll_chat/agents/voice_agent.py) | 直接继承 `BaseToolAgent`，强制微搜双工具白名单 |
| [`tool_policy.py`](../../qll_chat/cascaded_voice_agent/tool_policy.py) | 无第三方依赖的双工具 fail-closed 策略 |
| [`qll_chat/chat.py`](../../qll_chat/chat.py) | 初始化 `VoiceAgent`，通过 `functionName=voiceAgent` 路由 |
| [`resources/common_data/prompt_template/voice_agent.md`](../../resources/common_data/prompt_template/voice_agent.md) | 口语化回答、查询和安全约束 |
| [`qll_chat/cascaded_voice_agent/existing_agent.py`](../../qll_chat/cascaded_voice_agent/existing_agent.py) | `/chat` SSE 适配、工具状态、历史和取消 |

### 3.2 自研编排内核

| 文件 | 完整职责 |
|---|---|
| [`frames.py`](../../qll_chat/cascaded_voice_agent/frames.py) | 全部 Frame 契约与 PCM 校验 |
| [`processor.py`](../../qll_chat/cascaded_voice_agent/processor.py) | 优先队列、串联、错误传播和处理器清理 |
| [`pipeline.py`](../../qll_chat/cascaded_voice_agent/pipeline.py) | 启动、入队、致命错误和有界关闭 |
| [`vad.py`](../../qll_chat/cascaded_voice_agent/vad.py) | 能量 VAD、单调 `turn_id` 和插话事件 |

### 3.3 ASR 与 TTS Adapter

| 文件 | 完整职责 |
|---|---|
| [`providers/aliyun_stt.py`](../../qll_chat/cascaded_voice_agent/providers/aliyun_stt.py) | Paraformer 话轮生命周期、前导音频和线程回调 |
| [`tts_chunker.py`](../../qll_chat/cascaded_voice_agent/tts_chunker.py) | 中文增量文本分片 |
| [`tts.py`](../../qll_chat/cascaded_voice_agent/tts.py) | TTS 后台任务、插话取消和旧话轮抑制 |
| [`providers/cosyvoice_tts.py`](../../qll_chat/cascaded_voice_agent/providers/cosyvoice_tts.py) | CosyVoice 流式 SDK、回调和取消确认 |

### 3.4 会话、WebRTC 与服务入口

| 文件 | 完整职责 |
|---|---|
| [`contracts.py`](../../qll_chat/cascaded_voice_agent/contracts.py) | 可信用户上下文和媒体 seam |
| [`session.py`](../../qll_chat/cascaded_voice_agent/session.py) | 装配全链路、空闲关闭和媒体输出 |
| [`transport.py`](../../qll_chat/cascaded_voice_agent/transport.py) | aiortc SDP、上下行音轨和 DataChannel |
| [`session_registry.py`](../../qll_chat/cascaded_voice_agent/session_registry.py) | 短期 token、单次连接和会话注册 |
| [`settings.py`](../../qll_chat/cascaded_voice_agent/settings.py) | 配置解析、音频规格和 ICE 校验 |
| [`services/cascaded_voice_agent_service.py`](../../services/cascaded_voice_agent_service.py) | aiohttp 建会话、offer、关闭和统一回收 |
| [`default.conf`](../../resources/application/cascaded_voice_agent/configs/default.conf) | 非敏感默认配置和环境变量入口 |
| [`requirements_py3.12.11.in`](../../requirements_py3.12.11.in) | `aiohttp`、`aiortc`、DashScope 等依赖 |

`services/voice_agent_service.py` 和 `qll_chat/voice_agent/` 属于此前端到端方向的未完成骨架，不是本方案入口。第一版只启动 `services/cascaded_voice_agent_service.py`。

## 4. 从零搭建顺序

### 4.1 建立 Python 3.12.11 环境

项目依赖文件以 Python 3.12.11 为目标。不要使用当前损坏的 `qll_chat-master/.venv`：它基于 Python 3.13.5，启动器无法加载标准库，而且 3.13 已移除当前 VAD 使用的 `audioop`。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements_py3.12.11.in
```

关键版本：

```text
aiohttp==3.14.3
aiortc==1.15.0
dashscope==1.27.7
```

### 4.2 接入现有 LLM 和工具链

`VoiceAgent` 直接继承 `BaseToolAgent`，因此模型调用、工具循环、MCP 执行、SSE、日志和历史协议都复用现有实现。它不继承 `GenericAgent`，也不重新实现一套 LLM 循环。

可信请求进入 `/chat` 时必须包含：

```json
{
  "tenantId": "可信租户",
  "userId": "可信用户",
  "channel": "writer",
  "functionName": "voiceAgent",
  "algExtra": {},
  "question": "由 ASR final 填充"
}
```

`ExistingAgentSSEClient` 会再次覆盖 `tenantId`、`userId`、`functionName`、`questionId`、`answerId`、`question` 和 `history`。手机不能直接构造这份模板。

### 4.3 装配算法 Pipeline

生产装配代码位于 `CascadedVoiceSession._create_processors()`，顺序固定：

```python
return [
    EnergyVADProcessor(
        threshold=settings.vad_energy_threshold,
        start_ms=settings.vad_start_ms,
        stop_ms=settings.vad_stop_ms,
    ),
    AliyunStreamingSTTProcessor(
        settings.aliyun_api_key,
        settings.aliyun_stt_model,
        settings.audio_in_sample_rate,
    ),
    ExistingAgentProcessor(
        ExistingAgentSSEClient(
            settings.agent_chat_url,
            trusted_user_context,
            timeout_seconds=settings.agent_timeout_seconds,
        )
    ),
    StreamingTTSProcessor(
        CosyVoiceTTSService(
            settings.aliyun_api_key,
            settings.cosyvoice_model,
            settings.cosyvoice_voice,
        ),
        settings.tts_chunk_max_chars,
    ),
]
```

`CallbackProcessor` 由 Session 自动追加，用于把音频和状态发回 WebRTC。调用方不能调整 Processor 顺序，否则 VAD、ASR final、Agent turn 和 TTS 取消语义会失效。

### 4.4 配置运行环境

最小环境变量：

```powershell
$env:CHAT_ENV = "dev"
$env:DASHSCOPE_API_KEY = "<dashscope-key>"
$env:VOICE_INTERNAL_SERVICE_TOKEN = "<业务后端到算法服务的内部凭证>"
$env:VOICE_AGENT_CHAT_URL = "http://127.0.0.1:1801/chat"
$env:VOICE_WEBRTC_ICE_URLS = "stun:stun.example.com:3478,turn:turn.example.com:3478"
$env:VOICE_WEBRTC_ICE_USERNAME = "<turn-user>"
$env:VOICE_WEBRTC_ICE_CREDENTIAL = "<turn-password>"
```

本机同网段验证可以暂时不配置 TURN。手机移动网络和生产环境必须在 P9 验证 TURN，不能用“Wi-Fi 能连通”代替公网可用结论。

### 4.5 启动现有 `/chat`

VoiceAgent 使用现有 Flask `/chat` 入口。配置默认指向：

```text
http://127.0.0.1:1801/chat
```

这是 `services/gunicorn_chat_service.py` 当前 dev 启动示例的端口；private 示例使用 `18001`。部署端口不同时必须通过 `VOICE_AGENT_CHAT_URL` 覆盖。`8009` 对应的 `services/xiaoe_agent_flask.py` 没有 `/chat` 路由，不能作为本方案 LLM 接口；`/voice_interaction` 也不是本方案接口。

Windows 本地联调可在项目根目录启动 Flask 开发服务；生产 Linux 仍使用仓库已有 Gunicorn 命令：

```powershell
$env:CHAT_ENV = "dev"
.\.venv\Scripts\python.exe -m flask --app services.gunicorn_chat_service:app run --host 127.0.0.1 --port 1801
```

### 4.6 启动语音算法服务

```powershell
.\.venv\Scripts\python.exe services\cascaded_voice_agent_service.py
```

健康检查：

```http
GET /health
```

预期：

```json
{"status":"ok","service":"cascaded_voice_agent"}
```

## 5. 前端、业务后端和算法职责

| 层 | 必须实现 | 禁止承担 |
|---|---|---|
| 手机 App | 麦克风、扬声器、PeerConnection、DataChannel、音频焦点、前后台和重连 | DashScope Key、完整 Agent 模板、工具权限 |
| 业务后端 | 校验登录态、构造可信 tenant/user/agent_request、调用建会话接口 | 转发原始音频、运行 ASR/TTS |
| 算法服务 | token、SDP、WebRTC、VAD、ASR、Agent SSE、TTS、取消和回收 | 用户登录鉴权、业务权限推断 |
| qll_chat `/chat` | VoiceAgent、模型循环、MCP、微搜工具、SSE 和日志 | WebRTC 音频处理 |

## 6. 完整建会话协议

### 6.1 业务后端创建可信会话

```http
POST /voice/sessions
X-Internal-Service-Token: <VOICE_INTERNAL_SERVICE_TOKEN>
Content-Type: application/json
```

```json
{
  "tenant_id": "100002",
  "user_id": "732849986415722499",
  "agent_request": {
    "tenantId": "100002",
    "userId": "732849986415722499",
    "channel": "writer",
    "functionName": "voiceAgent",
    "algExtra": {},
    "history": []
  }
}
```

返回：

```json
{
  "session_id": "服务端生成",
  "voice_token": "短期随机令牌",
  "expires_at": 0,
  "signaling_url": "/voice/sessions/{session_id}/offer"
}
```

### 6.2 手机交换 SDP

```http
POST /voice/sessions/{session_id}/offer
Authorization: Bearer <voice_token>
Content-Type: application/json
```

```json
{
  "sdp": "<localDescription.sdp>",
  "type": "offer"
}
```

响应为 `sdp/type/session_id`。同一 token 的第二次 offer 会被拒绝；手机提交 `ice_servers` 也会被拒绝。

### 6.3 显式结束

```http
DELETE /voice/sessions/{session_id}
Authorization: Bearer <voice_token>
```

断开 PeerConnection、空闲超时和服务关闭也会进入相同清理路径。

## 7. DataChannel 事件

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

首版手机只需要根据事件切换状态，不需要显示字幕和引用卡片。`assistant.tool` 只表达工具正在运行，不传递内部推理文本。`assistant.answer.completed` 只表示 Agent 文本和引用已经结束，不表示音频播放完成；收到带当前 `turn_id` 的后续 `assistant.listening` 才表示 TTS 已完成并重新进入监听。首次无 `turn_id` 的 `assistant.listening` 表示会话已经就绪。

## 8. 插话和旧话轮隔离

插话时按以下顺序执行：

1. VAD 生成更大的 `turn_id` 并发出 `InterruptFrame`。
2. Agent Processor 先把 Interrupt 下传，再取消旧 HTTP SSE。
3. TTS Processor 先把 Interrupt 下传，让媒体层立即清空 PCM。
4. TTS 取消 CosyVoice，并把当前 `turn_id` 设为新的最小有效话轮。
5. 已排队但 `turn_id` 更小的 `AgentTextFrame/AgentEndFrame` 被丢弃。
6. 新话轮 ASR final 才能启动新的 Agent 和 TTS。

这个门槛不能只依赖“取消任务成功”：网络回调和队列中已经存在的数据仍可能在取消后到达。

## 9. 错误和资源释放

| 位置 | 非致命错误 | 致命错误/关闭 |
|---|---|---|
| ASR | 当前话轮返回 `ErrorFrame` | Pipeline 异常时关闭会话 |
| Agent | 播报短失败提示 | 用户插话取消当前请求 |
| TTS | 当前话轮返回 `ErrorFrame` | 会话关闭时取消 SDK 任务 |
| WebRTC | 音轨异常触发 disconnect | 关闭 PeerConnection 和 Reader Task |
| Pipeline | 记录第一个 fatal error | 最多等待 10 秒后强制取消 |
| Session | 输出 `session.error` | 显式、断线、空闲、服务关闭统一回收 |

日志不得记录原始 PCM、完整 SDP、API Key、voice token 或完整 Agent 权限模板。

## 10. 验证顺序

### P4：现有 Agent

1. 启动真实 `/chat`。
2. 使用可信模板分别直调 `/chat` 和通过 `ExistingAgentSSEClient` 调用。
3. 确认最终语义一致，SSE 至少出现 `3 → 4 → 0/1`。
4. 确认工具集合只有 `search_weisou` 和 `read_weisou_document`。

仓库提供默认跳过的真实联调测试 `test/test_cascaded_voice_agent_live.py`。可信请求必须来自目标环境中业务后端已完成登录和权限校验后生成的真实 `/chat` 请求，不能根据文档示例臆造；将其保存为仓库外 JSON 文件并清空历史业务内容，不能提交。测试会覆盖本轮 `question`、`questionId`、`answerId`、`history` 和 `functionName`，但会保留微搜权限所需的其他可信字段。开启后一次完整运行会产生两次普通问答和一次微搜调用：

```powershell
$env:RUN_CASCADED_VOICE_LIVE = "1"
$env:VOICE_AGENT_CHAT_URL = "http://127.0.0.1:1801/chat"
$env:CASCADED_VOICE_AGENT_REQUEST_FILE = "D:\secure\voice-agent-request.json"
$env:CASCADED_VOICE_PLAIN_QUESTION = "请用一句话介绍你能做什么"
$env:CASCADED_VOICE_WEISOU_QUESTION = "请用微搜查询指定资料并概括内容"
python -m unittest test.test_cascaded_voice_agent_live -v
```

测试自动校验两条调用路径都有非空终态、微搜状态顺序和工具白名单。LLM 输出具有随机性，最终答案的语义一致性必须人工复核，不能用字符串全等代替。

### P5：真实 ASR

1. 连续发送多轮 16 kHz mono PCM16。
2. 每轮只有一个 final。
3. 正常、异常、取消后 Recognition 都释放。

P5/P6 共用默认跳过的云服务联调入口。PCM 文件必须是无 WAV 头的 16 kHz、单声道、16-bit little-endian 语音；ASR 会连续发送同一段音频两轮，测试按真实时长发送，因此会消耗两倍音频时长对应的云服务额度：

```powershell
$env:RUN_CASCADED_VOICE_CLOUD_LIVE = "1"
$env:DASHSCOPE_API_KEY = "<dashscope-key>"
$env:CASCADED_VOICE_ASR_PCM_FILE = "D:\secure\voice-16k-mono-s16le.pcm"
# 以下配置有默认值，只有账号实际开通的模型或音色不同才覆盖
$env:CASCADED_VOICE_ASR_MODEL = "paraformer-realtime-v2"
$env:CASCADED_VOICE_TTS_MODEL = "cosyvoice-v2"
$env:CASCADED_VOICE_TTS_VOICE = "longxiaochun_v2"
$env:CASCADED_VOICE_TTS_TEXT = "你好，这是一段级联语音测试。"
python -m unittest test.test_cascaded_voice_agent_live.CascadedVoiceCloudLiveTest -v
```

测试不保存输入音频、识别文本或合成 PCM；失败日志只保留测试断言和 SDK 异常。两轮必须分别且仅产生一个非空 final。

### P6：真实 TTS

1. 增量发送中文文本。
2. 确认首音频能够提前返回。
3. 播放中插话，旧回调和旧 PCM 不再恢复。

上面的云服务联调会把测试文本拆成两段增量发送，确认 `finish()` 之前已收到首个 PCM 块，并检查所有输出均按 PCM16 采样对齐。真实取消后的迟到回调由现有 SDK 替身测试覆盖；手机扬声器播放中的插话体验仍属于 P9 真机门禁。

### P7：完整级联

1. 不接 WebRTC，使用内存 MediaSession 连续完成至少 10 轮。
2. 替换成真实 ASR、`/chat + 微搜`、TTS 重跑。
3. 检查没有残留 `voice:*` 任务和云连接。

P7 使用同一份仓库外 PCM，但只替换传输层为内存实现，生产 VAD、ASR、VoiceAgent SSE 和 TTS 均不替换：

```powershell
$env:RUN_CASCADED_VOICE_PIPELINE_LIVE = "1"
# 同时设置上文 P4、P5/P6 所需的 URL、可信 JSON、PCM、API Key 和模型变量
$env:CASCADED_VOICE_VAD_THRESHOLD = "450"
python -m unittest test.test_cascaded_voice_agent_live.CascadedVoicePipelineLiveTest -v
```

该门禁验证一次真实话轮、Agent 文本终态、TTS 完成后回到 `assistant.listening`、非空音频、无 `session.error` 且关闭后无残留 `voice:*` 任务。不能把较早到达的 `assistant.answer.completed` 当成音频播放完成。十轮稳定性已由内存集成测试覆盖；真实服务十轮会重复产生云端费用，应留到 P10 小范围试用而不是每次本地回归执行。

### P8：WebRTC

`test/test_cascaded_voice_agent_transport.py` 已使用 `aiortc==1.15.0` 建立本机双 PeerConnection：上行音频、下行音频和 DataChannel 均可传输，重复 offer 被拒绝，关闭后没有残留 `voice:*` 任务。`test/test_cascaded_voice_agent_session_registry.py` 继续覆盖错误 token、重复连接和令牌移除后不可复用。未安装 aiortc 的轻量环境会明确跳过 transport 测试，不能把 skip 当成通过。

`test/test_cascaded_voice_agent_service.py` 通过 aiohttp 内存服务验证内部服务凭证、可信身份、一次性 voice token、客户端 ICE 注入拒绝和显式关闭；媒体工厂使用依赖注入，不会在该测试中建立真实 PeerConnection。

### P9：手机

只有 P4～P8 的真实环境门禁完成后才能开始。必须覆盖 Wi-Fi、4G/5G、TURN、回声消除、音频焦点、来电、前后台和网络切换。

## 11. 当前执行顺序

```text
重建 Python 3.12.11 环境
  → 安装 requirements_py3.12.11.in
  → P4 /chat + VoiceAgent + 微搜
  → P5 真实 ASR
  → P6 真实 TTS
  → P7 真实完整级联
  → P8 部署环境复核
  → P9 手机联调
  → P10 小范围试用
  → P11 多智能体异步升级
```

不能因为本地 mock 或双 PeerConnection 通过，就跳过真实供应商、真实工具和手机网络门禁。

## 12. 完整源码快照
本节是当前第一版新增模块的完整可复制快照。为避免误导，仓库源码仍是后续修复的唯一事实源；现有大型文件只列本功能的接入差异。

### `qll_chat/agents/voice_agent.py`

````python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音交互主智能体：复用 BaseToolAgent 循环，首版只开放微搜配套工具。"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List

from project_utils.text_process.file_operation import load_markdown
from qll_chat.agents.base_tool_agent import AgentRunState, BaseToolAgent, PromptSpec, ToolMeta
from qll_chat.cascaded_voice_agent.tool_policy import (
    ALLOWED_VOICE_TOOL_NAMES,
    filter_voice_tools,
)
from qll_chat.core.context_builder import ContextBuilder
from qll_chat.mcp.mcp_search import MCPSearch
from qll_chat.mcp.mcp_tool_client import MCPToolClient
from qll_chat.prompts.xiaoe_chat import XiaoeChat
from qll_chat.retrieval.weisou_context import prepare_weisou_context
from qll_chat.retrieval.weisou_system_type_client import WeisouSystemTypeClient
from qll_chat.utils.weisou_modules import (
    build_weisou_e9_module_registry,
    build_weisou_module_registry,
)


logger = logging.getLogger(__name__)


class VoiceAgent(BaseToolAgent):
    """面向语音播报的 LLM Agent，首版只允许调用微搜查询和正文读取。"""

    _ALLOWED_TOOL_NAMES = ALLOWED_VOICE_TOOL_NAMES
    _ENABLED_LOCAL_TOOLS: List[str] = []

    def __init__(self, config: Dict[str, Any], xiaoe_chat_config: Dict[str, Any]) -> None:
        super().__init__(
            mcp_tool_client=MCPToolClient(config),
            xiaoe_chat=XiaoeChat(xiaoe_chat_config),
            context_builder=ContextBuilder(),
            config=config,
        )
        self.mcp_search = MCPSearch(config)
        self.system_prompt = load_markdown(
            "resources/common_data/prompt_template/voice_agent.md"
        )
        self.weisou_module_registry = build_weisou_module_registry(
            config.get("weisou_modules")
        )
        self.weisou_e9_module_registry = build_weisou_e9_module_registry(
            config.get("weisou_e9_modules")
        )
        self.weisou_system_type_client = WeisouSystemTypeClient(
            url=str(config.get("weisou_system_type_url") or ""),
            timeout=float(config.get("weisou_system_type_timeout", 3.0)),
        )
        self.weaver_tenant_ids = {
            str(item) for item in config.get("weaver_tenantId", [])
        }
        self.deploy_env = str(config.get("deploy_env") or "").strip().lower()
        # 首版不接入动态 provider，避免它在微搜白名单之后重新加入其他工具。
        self.tool_providers = []

    async def preprocess(self, req: Dict[str, Any], state: AgentRunState) -> None:
        if not str(req.get("tenantId") or "").strip() or not str(
            req.get("userId") or ""
        ).strip():
            raise ValueError("VoiceAgent 请求缺少 tenantId 或 userId")

        alg_extra = req.setdefault("algExtra", {})
        if not isinstance(alg_extra, dict):
            raise ValueError("VoiceAgent 请求的 algExtra 必须是对象")
        search_range = alg_extra.setdefault("searchRange", {})
        if not isinstance(search_range, dict):
            raise ValueError("VoiceAgent 请求的 algExtra.searchRange 必须是对象")

        # 能力由 VoiceAgent 固定收紧，不能由请求打开其他检索工具。
        search_range.update(
            {"innerCorpus": 0, "network": 0, "weisou": 1, "db": 0}
        )
        req["_disabled_retrieval_tool_names"] = []
        req["max_tool_rounds"] = 4
        # 禁止 BaseToolAgent 从历史记录恢复其他 MCP，保证单工具边界不会被绕过。
        req["max_historical_tool_recoveries"] = 0

        await prepare_weisou_context(
            req=req,
            enabled=True,
            system_type_client=self.weisou_system_type_client,
            module_registry=self.weisou_module_registry,
            e9_module_registry=self.weisou_e9_module_registry,
            weaver_tenant_ids=self.weaver_tenant_ids,
            deploy_env=self.deploy_env,
            log_tag="VoiceAgent",
        )
        await super().preprocess(req, state)

    def get_system_prompt(
        self,
        req: Dict[str, Any],
        state: AgentRunState,
    ) -> PromptSpec:
        allowed_modules = list(req.get("_weisou_allowed_modules") or [])
        module_text = "、".join(allowed_modules) if allowed_modules else "当前不可用"
        prompt = self.system_prompt.replace("$_current_datetime_$", state.cur_datetime)
        prompt = prompt.replace("$_allowed_weisou_modules_$", module_text)
        return PromptSpec(
            prompt=prompt,
            template_name="voice_agent",
            prompt_vars={"allowed_weisou_modules": allowed_modules},
        )

    def get_tools(
        self,
        req: Dict[str, Any],
        state: AgentRunState,
    ) -> List[Dict[str, Any]]:
        req["_local_tool_names"] = []
        return list(state.tool_schemas)

    async def discover_tools(self, req: Dict[str, Any], state: AgentRunState) -> None:
        search_req = dict(req)
        search_req["_safe_name_assignment"] = state.safe_name_assignment
        search_req["_force_retrieval_mcp_injection"] = True
        search_req["openai_tool_schemas"] = []
        search_req["tool2mcp"] = {}
        await self.mcp_search.inject_retrieval_tools_only(search_req)

        registry = {
            name: ToolMeta.from_mapping(meta)
            for name, meta in (search_req.get("tool2mcp") or {}).items()
        }
        schemas, registry = self._keep_weisou_tools_only(
            search_req,
            list(search_req.get("openai_tool_schemas") or []),
            registry,
        )
        schemas = self._constrain_weisou_schemas(req, schemas, registry)

        state.tool_schemas = schemas
        state.tool_registry = registry
        state.safe_name_assignment = dict(
            search_req.get("_safe_name_assignment") or state.safe_name_assignment
        )
        req["_safe_name_assignment"] = dict(state.safe_name_assignment)
        req["openai_tool_schemas"] = schemas
        req["tool2mcp"] = {
            name: meta.__dict__ for name, meta in registry.items()
        }
        req["tool_name_map"] = dict(search_req.get("tool_name_map") or {})
        req["loaded_mcp_ids"] = list(
            dict.fromkeys(str(meta.mcp_id) for meta in registry.values() if meta.mcp_id)
        )
        req["mcp_discovery_path"] = "voice_weisou_only"
        logger.info(
            "VoiceAgent 只读工具加载完成 | count=%s names=%s",
            len(registry),
            list(registry),
        )

    @classmethod
    def _keep_weisou_tools_only(
        cls,
        req: Dict[str, Any],
        schemas: List[Dict[str, Any]],
        registry: Dict[str, ToolMeta],
    ) -> tuple[List[Dict[str, Any]], Dict[str, ToolMeta]]:
        return filter_voice_tools(req, schemas, registry)

    @staticmethod
    def _constrain_weisou_schemas(
        req: Dict[str, Any],
        schemas: List[Dict[str, Any]],
        registry: Dict[str, ToolMeta],
    ) -> List[Dict[str, Any]]:
        allowed_modules = list(req.get("_weisou_allowed_modules") or [])
        if not allowed_modules:
            registry.clear()
            return []

        constrained: List[Dict[str, Any]] = []
        for schema in schemas:
            safe_name = str((schema.get("function") or {}).get("name") or "")
            meta = registry.get(safe_name)
            if meta is None or meta.raw_name != "search_weisou":
                constrained.append(schema)
                continue
            updated = copy.deepcopy(schema)
            function = updated.setdefault("function", {})
            parameters = function.setdefault("parameters", {"type": "object"})
            properties = parameters.setdefault("properties", {})
            properties["module"] = {
                "type": "array",
                "description": "本次允许检索的微搜模块，只能从 enum 中选择。",
                "items": {"type": "string", "enum": allowed_modules},
                "minItems": 1,
                "uniqueItems": True,
            }
            required = [str(item) for item in parameters.get("required") or []]
            if "module" not in required:
                required.append("module")
            parameters["required"] = required

            search_type = req.get("_weisou_search_type")
            if search_type in {0, 1}:
                properties["search_type"] = {
                    "type": "integer",
                    "enum": [search_type],
                    "default": search_type,
                    "description": "本轮固定的微搜搜索类型：0=全文，1=标题。",
                }
            constrained.append(updated)
        return constrained

    def get_tool_request_headers(
        self,
        tool_meta: ToolMeta,
        req: Dict[str, Any],
        state: AgentRunState,
    ) -> Dict[str, str]:
        headers = super().get_tool_request_headers(tool_meta, req, state)
        if (tool_meta.raw_name or "") in self._ALLOWED_TOOL_NAMES:
            system_type = req.get("_weisou_system_type")
            if system_type in {0, 1}:
                headers["X-Weisou-System-Type"] = str(system_type)
        return headers
````

### `qll_chat/cascaded_voice_agent/tool_policy.py`

````python
"""VoiceAgent 第一版的固定工具权限策略。"""

from collections.abc import Mapping
from typing import Any


ALLOWED_VOICE_TOOL_NAMES = frozenset(
    {"search_weisou", "read_weisou_document"}
)


def filter_voice_tools(
    req: Mapping[str, Any],
    schemas: list[dict[str, Any]],
    registry: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """同时过滤模型可见 schema 和执行注册表，未知工具默认拒绝。"""
    tool_name_map = req.get("tool_name_map") or {}
    raw_safe_to_raw = (
        tool_name_map.get("safe_to_raw") or {}
        if isinstance(tool_name_map, Mapping)
        else {}
    )
    safe_to_raw = raw_safe_to_raw if isinstance(raw_safe_to_raw, Mapping) else {}

    def allowed(safe_name: str) -> bool:
        meta = registry.get(safe_name)
        if meta is None:
            return False
        raw_name = str(
            getattr(meta, "raw_name", "")
            or safe_to_raw.get(safe_name)
            or safe_name
        )
        return raw_name in ALLOWED_VOICE_TOOL_NAMES

    filtered_registry = {
        name: meta for name, meta in registry.items() if allowed(name)
    }
    filtered_schemas = [
        schema
        for schema in schemas
        if allowed(str((schema.get("function") or {}).get("name") or ""))
    ]
    return filtered_schemas, filtered_registry
````

### 现有文件接入差异

以下是 `qll_chat/chat.py` 与依赖清单的完整相关差异；未复制这两个大型既有文件的无关内容。

````diff
diff --git a/qll_chat/chat.py b/qll_chat/chat.py
index a5c6bfd2..c195ea46 100644
--- a/qll_chat/chat.py
+++ b/qll_chat/chat.py
@@ -56,6 +56,7 @@ from qll_chat.utils.oa_utils.utils import ConversationStatus, Status
 from qll_chat.application.avatar import AvatarPrompt
 from qll_chat.agents.avatar_agent import AvatarEMAgent, AvatarPageAgent
 from qll_chat.agents.generic_agent import GenericAgent
+from qll_chat.agents.voice_agent import VoiceAgent
 from qll_chat.agents.customer_qa_agent import CustomerQAAgent
 from qll_chat.agents.mcp_agent import MCPAgent
 from qll_chat.agents.oa_agent import OAAgent
@@ -457,6 +458,10 @@ class QAChat(BaseChat):
             xiaoe_chat_config=xiaoe_chat_config,
             qa_config=config
         )
+        self.voice_agent = VoiceAgent(
+            config=xiaoe_agent_config,
+            xiaoe_chat_config=xiaoe_chat_config,
+        )
         if skill_extension is not None:
             self.generic_agent.set_skill_extension(skill_extension)
         if sandbox_runtime is not None:
@@ -1643,6 +1648,9 @@ class QAChat(BaseChat):
             yield from self.openclaw(req)
         elif functionName == "writingBissRuleCheck":
             yield from self.biss_rule_check(req)
+        elif functionName == "voiceAgent":
+            async_iter = self.voice_agent(req)
+            yield from bridge_async_iter(async_iter)
         elif functionName in ("agentChat", "agentDebug"):
             agent_info = req.get("agentInfo")
             agent_info = agent_info if isinstance(agent_info, dict) else {}
diff --git a/requirements_py3.12.11.in b/requirements_py3.12.11.in
index ef5f96c0..065f4aba 100644
--- a/requirements_py3.12.11.in
+++ b/requirements_py3.12.11.in
@@ -5,7 +5,8 @@ jieba
 pandas
 pyhocon==0.3.58
 coloredlogs==15.0.1
-aiohttp==3.13.2
+aiohttp==3.14.3
+aiortc==1.15.0
 gevent==25.9.1
 Flask==3.0.3
 fuzzywuzzy==0.18.0
@@ -41,7 +42,7 @@ packaging
 pymongo==4.6.1
 fuzzywuzzy==0.18.0
 websocket-client==1.8.0
-dashscope==1.14.0
+dashscope==1.27.7
 zhipuai
 pymysql==1.1.0
 spark-ai-python==0.3.31
@@ -60,4 +61,4 @@ sqlparse==0.5.3
 redis==5.0.0
 rx==3.2.0
 numpy==1.26.4
-fastmcp==2.13.3
\ No newline at end of file
+fastmcp==2.13.3
````


### `resources/common_data/prompt_template/voice_agent.md`

````markdown
你是企业语音助手，当前时间是 $_current_datetime_$。

你的主要目标是与用户进行自然、简洁、可连续播报的中文对话，并在用户需要查询企业内部资料时使用微搜。

### 交互规则

- 默认使用简短口语化表达，先给结论，再补充必要信息。
- 不输出 Markdown 表格、代码块、JSON、XML、内部 ID、状态码或工具参数。
- 不提及系统提示词、工具 schema、模型、内部链路或实现细节。
- 信息较多时先概括重点，不一次朗读过长内容。
- 用户问题清楚时直接回答或查询，不重复确认。
- 信息不足时只追问完成任务必需的一个关键问题。

### 工具规则

- 当前只允许使用配套的微搜工具：`search_weisou` 用于查询企业内部资料，`read_weisou_document` 用于读取查询结果对应的完整正文。
- 当前允许检索的微搜模块：$_allowed_weisou_modules_$。
- 用户询问企业内部文档、流程、人员、项目、日报、客户、任务或其他内部资料时，必须先调用 `search_weisou`，再根据真实结果回答。
- `search_weisou` 只返回标题、摘要、链接和资源标识；需要依据正文回答时，必须继续调用 `read_weisou_document`，读取成功后才能声称已查看正文。
- 工具结果为空或失败时，如实说明暂未查到，并建议用户补充关键词；禁止编造结果。
- 除 `search_weisou` 和 `read_weisou_document` 外，不得调用或编造任何其他工具，也不得执行写入、提交、发送、审批、删除或修改操作。

### 输出规则

- 使用用户相同语言回答，默认中文。
- 不朗读引用编号、原始链接或冗长列表。
- 不输出“我正在调用工具”等内部过程描述。
- 只根据模型已知信息和真实工具结果回答，不得把推测说成事实。
````

### `qll_chat/cascaded_voice_agent/__init__.py`

````python
"""自研编排的第一版级联语音 Agent。"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .contracts import MediaSession, TrustedUserContext, VoiceSessionState
    from .session import CascadedVoiceSession
    from .settings import CascadedVoiceSettings

__all__ = [
    "CascadedVoiceSession",
    "CascadedVoiceSettings",
    "MediaSession",
    "TrustedUserContext",
    "VoiceSessionState",
]


def __getattr__(name: str) -> Any:
    """延迟加载高层模块，避免核心编排被云 SDK 依赖污染。"""
    if name == "CascadedVoiceSession":
        from .session import CascadedVoiceSession

        return CascadedVoiceSession
    if name == "CascadedVoiceSettings":
        from .settings import CascadedVoiceSettings

        return CascadedVoiceSettings
    if name in {"MediaSession", "TrustedUserContext", "VoiceSessionState"}:
        from .contracts import MediaSession, TrustedUserContext, VoiceSessionState

        return {
            "MediaSession": MediaSession,
            "TrustedUserContext": TrustedUserContext,
            "VoiceSessionState": VoiceSessionState,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
````

### `qll_chat/cascaded_voice_agent/contracts.py`

````python
"""语音服务信任边界上的稳定契约。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable, Mapping, Protocol


class VoiceSessionState(StrEnum):
    """一条实时语音会话的用户可见状态。"""

    CONNECTING = "connecting"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class TrustedUserContext:
    """业务后端创建的可信身份和现有 Agent 请求模板。"""

    tenant_id: str
    user_id: str
    voice_session_id: str
    agent_request_template: Mapping[str, Any]

    def validate(self) -> None:
        if not self.tenant_id or not self.user_id or not self.voice_session_id:
            raise ValueError("tenant_id、user_id 和 voice_session_id 不能为空")
        if not isinstance(self.agent_request_template, Mapping):
            raise ValueError("agent_request_template 必须是对象")
        required = ("channel", "functionName", "algExtra")
        missing = [name for name in required if name not in self.agent_request_template]
        if missing:
            raise ValueError(f"Agent 请求模板缺少字段：{', '.join(missing)}")
        template_tenant = str(self.agent_request_template.get("tenantId", self.tenant_id))
        template_user = str(self.agent_request_template.get("userId", self.user_id))
        if template_tenant != self.tenant_id or template_user != self.user_id:
            raise ValueError("Agent 请求模板中的身份与可信上下文不一致")


class MediaSession(Protocol):
    """WebRTC 媒体会话接口，编排层不依赖 aiortc 对象。"""

    session_id: str

    def bind_audio_input(self, handler: Callable[[bytes], Awaitable[None]]) -> None: ...

    def bind_disconnect(self, handler: Callable[[str], Awaitable[None]]) -> None: ...

    async def send_audio(self, audio: bytes) -> None: ...

    async def clear_audio(self) -> None: ...

    async def send_event(self, event: Mapping[str, Any]) -> None: ...

    async def close(self) -> None: ...
````

### `qll_chat/cascaded_voice_agent/frames.py`

````python
"""自研语音流水线内部传递的 Frame 定义。"""

from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Mapping


@dataclass(slots=True, kw_only=True)
class Frame:
    """所有流水线消息的基类。"""

    created_at: float = field(default_factory=monotonic)


class SystemFrame(Frame):
    """需要优先于普通数据处理的控制消息。"""


@dataclass(slots=True, kw_only=True)
class StartFrame(SystemFrame):
    session_id: str


@dataclass(slots=True, kw_only=True)
class StopFrame(SystemFrame):
    reason: str


@dataclass(slots=True, kw_only=True)
class InterruptFrame(SystemFrame):
    turn_id: int


@dataclass(slots=True, kw_only=True)
class InputAudioFrame(Frame):
    audio: bytes
    sample_rate: int = 16000
    channels: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.audio, bytes):
            raise ValueError("音频数据必须是 bytes")
        if self.sample_rate != 16000 or self.channels != 1:
            raise ValueError("第一版输入固定为 16 kHz 单声道 PCM16")
        if len(self.audio) % 2:
            raise ValueError("PCM16 音频必须包含完整的 2 字节采样")


@dataclass(slots=True, kw_only=True)
class UserSpeechStartedFrame(Frame):
    turn_id: int


@dataclass(slots=True, kw_only=True)
class UserSpeechStoppedFrame(Frame):
    turn_id: int


@dataclass(slots=True, kw_only=True)
class TranscriptFrame(Frame):
    turn_id: int
    text: str
    is_final: bool


@dataclass(slots=True, kw_only=True)
class AgentTextFrame(Frame):
    turn_id: int
    text: str


@dataclass(slots=True, kw_only=True)
class AgentEndFrame(Frame):
    turn_id: int
    answer: str
    references: tuple[Mapping[str, Any], ...] = ()


@dataclass(slots=True, kw_only=True)
class OutputAudioFrame(Frame):
    turn_id: int
    audio: bytes
    sample_rate: int = 24000
    channels: int = 1


@dataclass(slots=True, kw_only=True)
class StateFrame(Frame):
    event: str
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ErrorFrame(SystemFrame):
    component: str
    message: str
    fatal: bool = False
    turn_id: int | None = None
````

### `qll_chat/cascaded_voice_agent/processor.py`

````python
"""参考 Pipecat FrameProcessor 实现的最小异步处理器。"""

import asyncio
import itertools
import logging
from contextlib import suppress
from collections.abc import Awaitable, Callable

from .frames import ErrorFrame, Frame, StartFrame, StopFrame, SystemFrame

logger = logging.getLogger(__name__)

FrameCallback = Callable[[Frame], Awaitable[None]]
FatalErrorHandler = Callable[[ErrorFrame], None]


class FrameProcessor:
    """每个处理器拥有独立队列，保证同优先级 Frame 的到达顺序。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._next: FrameProcessor | None = None
        self._queue: asyncio.PriorityQueue[tuple[int, int, Frame]] = asyncio.PriorityQueue()
        self._counter = itertools.count()
        self._task: asyncio.Task[None] | None = None
        self._fatal_error_handler: FatalErrorHandler | None = None

    def link(self, next_processor: "FrameProcessor") -> "FrameProcessor":
        self._next = next_processor
        return next_processor

    def set_fatal_error_handler(self, handler: FatalErrorHandler) -> None:
        """由 Pipeline 注册统一致命错误处理入口。"""
        self._fatal_error_handler = handler

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError(f"处理器已经启动：{self.name}")
        self._task = asyncio.create_task(self._run(), name=f"voice:{self.name}")

    async def wait_closed(self) -> None:
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task

    async def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        await self.wait_closed()

    async def queue_frame(self, frame: Frame) -> None:
        if self._task is None:
            raise RuntimeError(f"处理器尚未启动：{self.name}")
        if self._task.done():
            raise RuntimeError(f"处理器已经关闭：{self.name}")
        priority = 0 if isinstance(frame, SystemFrame) else 10
        await self._queue.put((priority, next(self._counter), frame))

    async def push_frame(self, frame: Frame) -> None:
        if self._next is not None:
            await self._next.queue_frame(frame)

    async def process_frame(self, frame: Frame) -> None:
        """子类只处理自己关心的 Frame，其余 Frame 应继续下传。"""
        await self.push_frame(frame)

    async def on_start(self, frame: StartFrame) -> None:
        """处理器启动钩子。"""

    async def on_stop(self) -> None:
        """处理器清理钩子，只在处理任务退出时调用一次。"""

    async def _run(self) -> None:
        try:
            while True:
                _, _, frame = await self._queue.get()
                try:
                    if isinstance(frame, StartFrame):
                        await self.on_start(frame)
                    await self.process_frame(frame)
                    if isinstance(frame, StopFrame):
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("语音处理器执行失败：%s", self.name)
                    error = ErrorFrame(
                        component=self.name,
                        message=str(exc),
                        fatal=True,
                        turn_id=getattr(frame, "turn_id", None),
                    )
                    try:
                        await self.push_frame(error)
                    except Exception:
                        logger.exception("语音处理器错误事件下传失败：%s", self.name)
                    self._report_fatal_error(error)
                    await self._forward_control_until_stop()
                    return
                finally:
                    self._queue.task_done()
        finally:
            try:
                await self.on_stop()
            except Exception as exc:
                logger.exception("语音处理器清理失败：%s", self.name)
                self._report_fatal_error(
                    ErrorFrame(component=self.name, message=str(exc), fatal=True)
                )

    def _report_fatal_error(self, error: ErrorFrame) -> None:
        if self._fatal_error_handler is not None:
            self._fatal_error_handler(error)

    async def _forward_control_until_stop(self) -> None:
        """故障节点不再处理数据，但仍保证 Stop 能传播到整条流水线。"""
        while True:
            _, _, frame = await self._queue.get()
            try:
                if isinstance(frame, SystemFrame):
                    await self.push_frame(frame)
                if isinstance(frame, StopFrame):
                    return
            finally:
                self._queue.task_done()


class CallbackProcessor(FrameProcessor):
    """把末端 Frame 交还给 WebRTC 媒体会话。"""

    def __init__(self, callback: FrameCallback) -> None:
        super().__init__("output")
        self._callback = callback

    async def process_frame(self, frame: Frame) -> None:
        await self._callback(frame)
````

### `qll_chat/cascaded_voice_agent/pipeline.py`

````python
"""自研级联语音流水线的装配和生命周期。"""

import asyncio
import logging

from .frames import ErrorFrame, Frame, StartFrame, StopFrame
from .processor import FrameProcessor

logger = logging.getLogger(__name__)


class VoicePipeline:
    """一次性线性流水线，统一负责启动、入队、故障和幂等关闭。"""

    _NEW = "new"
    _RUNNING = "running"
    _STOPPING = "stopping"
    _STOPPED = "stopped"

    def __init__(self, processors: list[FrameProcessor], session_id: str) -> None:
        if not processors:
            raise ValueError("流水线至少需要一个处理器")
        self.processors = tuple(processors)
        self.session_id = session_id
        self._state = self._NEW
        self._lifecycle_lock = asyncio.Lock()
        self._stop_task: asyncio.Task[None] | None = None
        self._fatal_error: ErrorFrame | None = None
        for current, following in zip(processors, processors[1:]):
            current.link(following)
        for processor in processors:
            processor.set_fatal_error_handler(self._handle_fatal_error)

    @property
    def fatal_error(self) -> ErrorFrame | None:
        """返回流水线记录到的第一个致命错误。"""
        return self._fatal_error

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._state != self._NEW:
                raise RuntimeError("流水线只能启动一次")
            started: list[FrameProcessor] = []
            try:
                for processor in reversed(self.processors):
                    await processor.start()
                    started.append(processor)
                self._state = self._RUNNING
                await self.processors[0].queue_frame(
                    StartFrame(session_id=self.session_id)
                )
            except Exception:
                await asyncio.gather(
                    *(processor.cancel() for processor in started),
                    return_exceptions=True,
                )
                self._state = self._STOPPED
                raise

    async def queue_frame(self, frame: Frame) -> None:
        async with self._lifecycle_lock:
            if self._state != self._RUNNING:
                raise RuntimeError("流水线当前不接受新的 Frame")
            await self.processors[0].queue_frame(frame)

    async def stop(self, reason: str) -> None:
        async with self._lifecycle_lock:
            if self._state in {self._NEW, self._STOPPED}:
                return
            if self._stop_task is None:
                self._state = self._STOPPING
                self._stop_task = asyncio.create_task(
                    self._stop_processors(reason),
                    name=f"voice:pipeline-stop:{self.session_id}",
                )
            stop_task = self._stop_task
        await stop_task

    async def _stop_processors(self, reason: str) -> None:
        try:
            try:
                await self.processors[0].queue_frame(StopFrame(reason=reason))
            except RuntimeError:
                logger.exception("语音流水线入口已经关闭，强制取消剩余处理器")
                await asyncio.gather(
                    *(processor.cancel() for processor in self.processors),
                    return_exceptions=True,
                )
                return
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(processor.wait_closed() for processor in self.processors),
                    return_exceptions=True,
                ),
                timeout=10,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.error(
                        "语音流水线关闭处理器失败",
                        exc_info=(type(result), result, result.__traceback__),
                    )
        except TimeoutError:
            await asyncio.gather(
                *(processor.cancel() for processor in self.processors),
                return_exceptions=True,
            )
        finally:
            async with self._lifecycle_lock:
                self._state = self._STOPPED

    def _handle_fatal_error(self, error: ErrorFrame) -> None:
        """记录首个致命错误，并异步关闭包括故障节点在内的整条链路。"""
        if self._fatal_error is not None:
            return
        self._fatal_error = error
        if self._state == self._RUNNING and self._stop_task is None:
            asyncio.create_task(
                self.stop(f"fatal:{error.component}"),
                name=f"voice:pipeline-fatal:{self.session_id}",
            )
````

### `qll_chat/cascaded_voice_agent/vad.py`

````python
"""第一版服务端能量 VAD 处理器。"""

import audioop

from .frames import (
    Frame,
    InputAudioFrame,
    InterruptFrame,
    StateFrame,
    UserSpeechStartedFrame,
    UserSpeechStoppedFrame,
)
from .processor import FrameProcessor


class EnergyVADProcessor(FrameProcessor):
    """用 PCM 能量和连续时长判断说话开始、结束。"""

    def __init__(self, *, threshold: int, start_ms: int, stop_ms: int) -> None:
        super().__init__("vad")
        if threshold <= 0 or start_ms <= 0 or stop_ms <= 0:
            raise ValueError("VAD 阈值和时长必须大于 0")
        self._threshold = threshold
        self._start_ms = start_ms
        self._stop_ms = stop_ms
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._speaking = False
        self._turn_id = 0

    async def process_frame(self, frame: Frame) -> None:
        if not isinstance(frame, InputAudioFrame):
            await self.push_frame(frame)
            return

        duration_ms = len(frame.audio) * 1000 / (
            frame.sample_rate * frame.channels * 2
        )
        energy = audioop.rms(frame.audio, 2) if frame.audio else 0

        if energy >= self._threshold:
            self._speech_ms += duration_ms
            self._silence_ms = 0.0
            if not self._speaking and self._speech_ms >= self._start_ms:
                self._speaking = True
                self._turn_id += 1
                await self.push_frame(InterruptFrame(turn_id=self._turn_id))
                await self.push_frame(UserSpeechStartedFrame(turn_id=self._turn_id))
                await self.push_frame(
                    StateFrame(event="assistant.listening", data={"turn_id": self._turn_id})
                )
        else:
            self._speech_ms = 0.0
            if self._speaking:
                self._silence_ms += duration_ms
                if self._silence_ms >= self._stop_ms:
                    self._speaking = False
                    self._silence_ms = 0.0
                    await self.push_frame(UserSpeechStoppedFrame(turn_id=self._turn_id))

        # ASR 使用连续音频流，因此静音帧也必须继续传递。
        await self.push_frame(frame)
````

### `qll_chat/cascaded_voice_agent/existing_agent.py`

````python
"""把现有 qll_chat `/chat` SSE 链路接入语音流水线。"""

import asyncio
import copy
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import datetime
from typing import Any, Mapping

import aiohttp

from .contracts import TrustedUserContext
from .frames import (
    AgentEndFrame,
    AgentTextFrame,
    ErrorFrame,
    InterruptFrame,
    StateFrame,
    TranscriptFrame,
)
from .processor import FrameProcessor


class ExistingAgentSSEClient:
    """固定路由到 VoiceAgent，只替换本轮问题、标识和最小历史。"""

    def __init__(
        self,
        url: str,
        trusted_context: TrustedUserContext,
        *,
        timeout_seconds: float,
        max_history_turns: int = 20,
    ) -> None:
        trusted_context.validate()
        self._url = url
        self._trusted_context = trusted_context
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_history_turns = max_history_turns
        self._history = list(trusted_context.agent_request_template.get("history", []))
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def stream(self, question: str) -> AsyncIterator[Mapping[str, Any]]:
        await self.open()
        assert self._session is not None
        request_data = self._build_request(question)
        async with self._session.post(self._url, json=request_data) as response:
            if response.status != 200:
                raise RuntimeError(f"现有 Agent 服务返回 HTTP {response.status}")
            async for payload in self._iter_sse(response):
                yield payload

    def append_history(self, question: str, answer: str, function_name: str) -> None:
        self._history.append(
            {
                "id": uuid.uuid4().hex,
                "question": question,
                "answer": answer,
                "functionName": function_name,
                "askTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        self._history = self._history[-self._max_history_turns :]

    def _build_request(self, question: str) -> dict[str, Any]:
        request_data = copy.deepcopy(dict(self._trusted_context.agent_request_template))
        request_data.update(
            {
                "tenantId": self._trusted_context.tenant_id,
                "userId": self._trusted_context.user_id,
                # 路由属于算法服务安全策略，不能由旧模板或手机输入降级为其他 Agent。
                "functionName": "voiceAgent",
                "question": question,
                "questionId": str(time.time_ns()),
                "answerId": str(time.time_ns() + 1),
                "history": copy.deepcopy(self._history),
            }
        )
        return request_data

    @staticmethod
    async def _iter_sse(response: aiohttp.ClientResponse) -> AsyncIterator[Mapping[str, Any]]:
        data_lines: list[str] = []
        while not response.content.at_eof():
            raw = await response.content.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                if data_lines:
                    data = "\n".join(data_lines)
                    data_lines.clear()
                    payload = ExistingAgentSSEClient._decode_sse_data(data)
                    if payload is not None:
                        yield payload
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            payload = ExistingAgentSSEClient._decode_sse_data("\n".join(data_lines))
            if payload is not None:
                yield payload

    @staticmethod
    def _decode_sse_data(data: str) -> Mapping[str, Any] | None:
        if data.strip() == "[DONE]":
            return None
        payload = json.loads(data)
        return payload if isinstance(payload, dict) else None


class ExistingAgentProcessor(FrameProcessor):
    """把 ASR final 交给现有 Agent，并将最终可播报文本流向 TTS。"""

    def __init__(self, client: ExistingAgentSSEClient) -> None:
        super().__init__("existing-agent")
        self._client = client
        self._turn_task: asyncio.Task[None] | None = None

    async def process_frame(self, frame) -> None:
        if isinstance(frame, InterruptFrame):
            # 先清空下游播放缓冲，再等待 HTTP 流完成取消。
            await self.push_frame(frame)
            await self._cancel_turn()
            return
        if isinstance(frame, TranscriptFrame) and frame.is_final and frame.text.strip():
            await self._cancel_turn()
            self._turn_task = asyncio.create_task(
                self._run_turn(frame.turn_id, frame.text.strip()),
                name=f"voice:agent:{frame.turn_id}",
            )
            return
        await self.push_frame(frame)

    async def on_stop(self) -> None:
        await self._cancel_turn()
        await self._client.close()

    async def _run_turn(self, turn_id: int, question: str) -> None:
        streamed_text: list[str] = []
        final_answer = ""
        function_name = "generalChat"
        references: tuple[Mapping[str, Any], ...] = ()
        await self.push_frame(StateFrame(event="assistant.thinking", data={"turn_id": turn_id}))
        try:
            stream = self._client.stream(question)
            async with aclosing(stream):
                async for payload in stream:
                    try:
                        status = int(payload.get("status", -1))
                    except (TypeError, ValueError):
                        continue
                    if status in (3, 4):
                        await self.push_frame(
                            StateFrame(
                                event="assistant.tool",
                                data={"turn_id": turn_id, "status": status},
                            )
                        )
                    elif status == 0:
                        token = str(payload.get("token", ""))
                        if token:
                            streamed_text.append(token)
                            await self.push_frame(
                                AgentTextFrame(turn_id=turn_id, text=token)
                            )
                    elif status == 1:
                        token = str(payload.get("token", ""))
                        if token:
                            streamed_text.append(token)
                            await self.push_frame(
                                AgentTextFrame(turn_id=turn_id, text=token)
                            )
                        final_answer = str(payload.get("answer", "")).strip()
                        function_name = str(
                            (payload.get("extraData") or {}).get(
                                "functionName", "generalChat"
                            )
                        )
                        references = tuple(payload.get("reference_data") or ())
                        break
                    elif status == 11:
                        raise RuntimeError(
                            "第一版语音链路暂不支持需要客户端续跑的工具"
                        )

            if not final_answer:
                final_answer = "".join(streamed_text).strip()
            if not final_answer:
                raise RuntimeError("现有 Agent 流结束但没有最终答案")
            if not streamed_text:
                await self.push_frame(AgentTextFrame(turn_id=turn_id, text=final_answer))
            self._client.append_history(question, final_answer, function_name)
            await self.push_frame(
                AgentEndFrame(turn_id=turn_id, answer=final_answer, references=references)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.push_frame(
                ErrorFrame(
                    component="existing-agent",
                    message=str(exc),
                    fatal=False,
                    turn_id=turn_id,
                )
            )
            fallback = "抱歉，这次处理没有成功，请稍后再试。"
            await self.push_frame(AgentTextFrame(turn_id=turn_id, text=fallback))
            await self.push_frame(AgentEndFrame(turn_id=turn_id, answer=fallback))

    async def _cancel_turn(self) -> None:
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            try:
                await self._turn_task
            except asyncio.CancelledError:
                pass
        self._turn_task = None
````

### `qll_chat/cascaded_voice_agent/tts_chunker.py`

````python
"""供应商无关的中文流式 TTS 文本分片器。"""


class SentenceChunker:
    """尽量按中文标点输出完整语义片段，不修改原始文本。"""

    _BOUNDARIES = frozenset("。！？；，!?;,\n")

    def __init__(self, max_chars: int = 80) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars 必须大于 0")
        self._max_chars = max_chars
        self._buffer = ""

    def add(self, text: str) -> tuple[str, ...]:
        if not isinstance(text, str):
            raise TypeError("TTS 文本必须是字符串")
        if not text:
            return ()
        chunks: list[str] = []
        for char in text:
            self._buffer += char
            if char in self._BOUNDARIES or len(self._buffer) >= self._max_chars:
                chunks.append(self._take_buffer())
        return tuple(chunks)

    def flush(self) -> str:
        return self._take_buffer()

    def reset(self) -> None:
        self._buffer = ""

    def _take_buffer(self) -> str:
        chunk, self._buffer = self._buffer, ""
        return chunk
````

### `qll_chat/cascaded_voice_agent/tts.py`

````python
"""Agent 文本流到 CosyVoice 音频流的处理器。"""

import asyncio

from .frames import (
    AgentEndFrame,
    AgentTextFrame,
    ErrorFrame,
    InterruptFrame,
    OutputAudioFrame,
    StateFrame,
)
from .processor import FrameProcessor
from .providers.cosyvoice_tts import CosyVoiceTTSService
from .tts_chunker import SentenceChunker


class StreamingTTSProcessor(FrameProcessor):
    """独立任务执行 TTS，使控制队列仍可立即处理用户插话。"""

    def __init__(self, service: CosyVoiceTTSService, max_chunk_chars: int) -> None:
        super().__init__("cosyvoice-tts")
        self._service = service
        self._max_chunk_chars = max_chunk_chars
        self._turn_id: int | None = None
        self._minimum_turn_id = 0
        self._text_queue: asyncio.Queue[str | None] | None = None
        self._tts_task: asyncio.Task[None] | None = None

    async def process_frame(self, frame) -> None:
        if isinstance(frame, InterruptFrame):
            self._minimum_turn_id = max(self._minimum_turn_id, frame.turn_id)
            # 先让媒体层清空播放缓冲，再等待供应商确认释放连接。
            await self.push_frame(frame)
            await self._cancel_turn()
            return
        if isinstance(frame, AgentTextFrame):
            if frame.turn_id < self._minimum_turn_id or not frame.text:
                return
            await self._ensure_turn(frame.turn_id)
            assert self._text_queue is not None
            await self._text_queue.put(frame.text)
            return
        if isinstance(frame, AgentEndFrame):
            if frame.turn_id < self._minimum_turn_id:
                return
            if self._text_queue is not None and frame.turn_id == self._turn_id:
                await self._text_queue.put(None)
            await self.push_frame(frame)
            return
        await self.push_frame(frame)

    async def on_stop(self) -> None:
        await self._cancel_turn()
        await self._service.close()

    async def _ensure_turn(self, turn_id: int) -> None:
        if self._tts_task is not None and not self._tts_task.done() and turn_id == self._turn_id:
            return
        await self._cancel_turn()
        self._turn_id = turn_id
        self._text_queue = asyncio.Queue()
        self._tts_task = asyncio.create_task(
            self._run_turn(turn_id, self._text_queue),
            name=f"voice:tts:{turn_id}",
        )

    async def _run_turn(self, turn_id: int, text_queue: asyncio.Queue[str | None]) -> None:
        chunker = SentenceChunker(self._max_chunk_chars)
        audio_started = False
        error_reported = False

        async def on_audio(audio: bytes) -> None:
            nonlocal audio_started
            if turn_id != self._turn_id or not audio:
                return
            if not audio_started:
                audio_started = True
                await self.push_frame(
                    StateFrame(event="assistant.speaking", data={"turn_id": turn_id})
                )
            await self.push_frame(OutputAudioFrame(turn_id=turn_id, audio=audio))

        async def on_error(message: str) -> None:
            nonlocal error_reported
            if turn_id == self._turn_id and not error_reported:
                error_reported = True
                await self.push_frame(
                    ErrorFrame(
                        component="cosyvoice-tts",
                        message=message,
                        fatal=False,
                        turn_id=turn_id,
                    )
                )

        try:
            await self._service.start(on_audio, on_error)
            while True:
                text = await text_queue.get()
                if text is None:
                    tail = chunker.flush()
                    if tail.strip():
                        await self._service.send_text(tail)
                    await self._service.finish()
                    if turn_id == self._turn_id:
                        await self.push_frame(
                            StateFrame(
                                event="assistant.listening",
                                data={"turn_id": turn_id},
                            )
                        )
                    return
                for chunk in chunker.add(text):
                    if chunk.strip():
                        await self._service.send_text(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if turn_id == self._turn_id and not error_reported:
                error_reported = True
                await self.push_frame(
                    ErrorFrame(
                        component="cosyvoice-tts",
                        message=str(exc),
                        fatal=False,
                        turn_id=turn_id,
                    )
                )
        finally:
            chunker.reset()
            if self._tts_task is asyncio.current_task():
                self._tts_task = None
                self._text_queue = None
                self._turn_id = None

    async def _cancel_turn(self) -> None:
        turn_id = self._turn_id
        task = self._tts_task
        self._turn_id = None
        self._tts_task = None
        self._text_queue = None
        try:
            await self._service.cancel()
        except Exception as exc:
            await self.push_frame(
                ErrorFrame(
                    component="cosyvoice-tts",
                    message=f"取消语音合成失败：{exc}",
                    fatal=False,
                    turn_id=turn_id,
                )
            )
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
````

### `qll_chat/cascaded_voice_agent/settings.py`

````python
"""第一版自研级联语音服务配置。"""

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class CascadedVoiceSettings:
    """只保存算法服务需要的配置，敏感值由环境变量注入。"""

    aliyun_api_key: str
    aliyun_stt_model: str
    cosyvoice_model: str
    cosyvoice_voice: str
    agent_chat_url: str
    internal_service_token: str
    host: str = "0.0.0.0"
    port: int = 8010
    agent_timeout_seconds: float = 120.0
    session_idle_seconds: float = 300.0
    session_token_ttl_seconds: float = 120.0
    tts_chunk_max_chars: int = 80
    vad_energy_threshold: int = 450
    vad_start_ms: int = 120
    vad_stop_ms: int = 600
    audio_in_sample_rate: int = 16000
    audio_out_sample_rate: int = 24000
    webrtc_ice_urls: tuple[str, ...] = ()
    webrtc_ice_username: str = ""
    webrtc_ice_credential: str = ""

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "CascadedVoiceSettings":
        """从仓库现有 HOCON 配置结果构造设置。"""
        return cls(
            aliyun_api_key=str(config.get("aliyun_api_key", "")),
            aliyun_stt_model=str(config.get("aliyun_stt_model", "")),
            cosyvoice_model=str(config.get("cosyvoice_model", "")),
            cosyvoice_voice=str(config.get("cosyvoice_voice", "longxiaochun_v2")),
            agent_chat_url=str(config.get("agent_chat_url", "")),
            internal_service_token=str(config.get("internal_service_token", "")),
            host=str(config.get("host", "0.0.0.0")),
            port=int(config.get("port", 8010)),
            agent_timeout_seconds=float(config.get("agent_timeout_seconds", 120.0)),
            session_idle_seconds=float(config.get("session_idle_seconds", 300.0)),
            session_token_ttl_seconds=float(config.get("session_token_ttl_seconds", 120.0)),
            tts_chunk_max_chars=int(config.get("tts_chunk_max_chars", 80)),
            vad_energy_threshold=int(config.get("vad_energy_threshold", 450)),
            vad_start_ms=int(config.get("vad_start_ms", 120)),
            vad_stop_ms=int(config.get("vad_stop_ms", 600)),
            audio_in_sample_rate=int(config.get("audio_in_sample_rate", 16000)),
            audio_out_sample_rate=int(config.get("audio_out_sample_rate", 24000)),
            webrtc_ice_urls=cls._parse_ice_urls(config.get("webrtc_ice_urls", "")),
            webrtc_ice_username=str(config.get("webrtc_ice_username", "")),
            webrtc_ice_credential=str(config.get("webrtc_ice_credential", "")),
        )

    def validate(self) -> None:
        missing = [
            name
            for name in (
                "aliyun_api_key",
                "aliyun_stt_model",
                "cosyvoice_model",
                "cosyvoice_voice",
                "agent_chat_url",
                "internal_service_token",
            )
            if not str(getattr(self, name) or "").strip()
        ]
        if missing:
            raise ValueError(f"级联语音配置缺少字段：{', '.join(missing)}")
        if not 1 <= self.port <= 65535:
            raise ValueError("语音服务端口必须在 1～65535 之间")
        agent_url = urlsplit(self.agent_chat_url)
        if agent_url.scheme not in {"http", "https"} or not agent_url.netloc:
            raise ValueError("agent_chat_url 必须是有效的 HTTP(S) 地址")
        if self.agent_timeout_seconds <= 0 or self.session_idle_seconds <= 0:
            raise ValueError("超时时间必须大于 0")
        if self.session_token_ttl_seconds <= 0:
            raise ValueError("会话令牌有效期必须大于 0")
        if self.tts_chunk_max_chars <= 0:
            raise ValueError("tts_chunk_max_chars 必须大于 0")
        if (
            self.vad_energy_threshold <= 0
            or self.vad_start_ms <= 0
            or self.vad_stop_ms <= 0
        ):
            raise ValueError("VAD 阈值和起止时长必须大于 0")
        if self.audio_in_sample_rate != 16000:
            raise ValueError("第一版 ASR 输入固定为 16 kHz 单声道 PCM")
        if self.audio_out_sample_rate != 24000:
            raise ValueError("第一版 TTS 输出固定为 24 kHz 单声道 PCM")
        if bool(self.webrtc_ice_username.strip()) != bool(
            self.webrtc_ice_credential.strip()
        ):
            raise ValueError("WebRTC ICE 用户名和凭证必须同时配置")
        allowed_schemes = ("stun:", "stuns:", "turn:", "turns:")
        if any(
            not url.lower().startswith(allowed_schemes)
            for url in self.webrtc_ice_urls
        ):
            raise ValueError("WebRTC ICE 地址只允许 STUN/TURN 协议")

    def ice_servers(self) -> tuple[Mapping[str, Any], ...]:
        """构造只来自服务端配置的 aiortc ICE Server 参数。"""
        if not self.webrtc_ice_urls:
            return ()
        server: dict[str, Any] = {"urls": list(self.webrtc_ice_urls)}
        if self.webrtc_ice_username:
            server["username"] = self.webrtc_ice_username
            server["credential"] = self.webrtc_ice_credential
        return (server,)

    @staticmethod
    def _parse_ice_urls(value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            values = value.split(",")
        elif isinstance(value, (list, tuple)):
            values = value
        else:
            raise ValueError("webrtc_ice_urls 必须是逗号分隔字符串或数组")
        return tuple(str(item).strip() for item in values if str(item).strip())
````

### `qll_chat/cascaded_voice_agent/session.py`

````python
"""一个级联语音会话的完整生命周期。"""

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from contextlib import suppress

from .contracts import MediaSession, TrustedUserContext
from .frames import (
    AgentEndFrame,
    ErrorFrame,
    Frame,
    InputAudioFrame,
    InterruptFrame,
    OutputAudioFrame,
    StateFrame,
    UserSpeechStartedFrame,
)
from .pipeline import VoicePipeline
from .processor import CallbackProcessor, FrameProcessor
from .providers import AliyunStreamingSTTProcessor, CosyVoiceTTSService
from .settings import CascadedVoiceSettings
from .tts import StreamingTTSProcessor
from .vad import EnergyVADProcessor


logger = logging.getLogger(__name__)
ProcessorFactory = Callable[[TrustedUserContext], Sequence[FrameProcessor]]


class CascadedVoiceSession:
    """把编排、云 SDK、Agent SSE 和资源释放封装在一个深模块中。"""

    def __init__(
        self,
        settings: CascadedVoiceSettings,
        *,
        processor_factory: ProcessorFactory | None = None,
    ) -> None:
        self.settings = settings
        self._processor_factory = processor_factory
        self._media: MediaSession | None = None
        self._pipeline: VoicePipeline | None = None
        self._state = "new"
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._closed_event = asyncio.Event()
        self._last_activity = 0.0
        self._idle_task: asyncio.Task[None] | None = None

    async def run(self, media: MediaSession, user: TrustedUserContext) -> None:
        """装配并启动第一版 `VAD → ASR → 现有 Agent → TTS`。"""
        user.validate()
        self.settings.validate()
        if media.session_id != user.voice_session_id:
            raise ValueError("媒体会话与可信 voice_session_id 不一致")
        async with self._close_lock:
            if self._state != "new":
                raise RuntimeError("语音会话只能启动一次")
            self._state = "starting"
            self._media = media
        media.bind_audio_input(self._on_audio)
        media.bind_disconnect(self.close)

        try:
            processors = list(self._create_processors(user))
            if not processors:
                raise ValueError("语音会话至少需要一个算法处理器")
            processors.append(CallbackProcessor(self._handle_output))
            pipeline = VoicePipeline(processors, user.voice_session_id)
            self._pipeline = pipeline
            await pipeline.start()

            async with self._close_lock:
                if self._state != "starting":
                    close_task = self._close_task
                else:
                    self._state = "running"
                    self._last_activity = time.monotonic()
                    self._idle_task = asyncio.create_task(
                        self._watch_idle(),
                        name=f"voice:idle:{user.voice_session_id}",
                    )
                    close_task = None
            if close_task is not None:
                await asyncio.shield(close_task)
                raise RuntimeError("语音会话在启动期间已经关闭")

            await media.send_event(
                {"type": "session.ready", "session_id": media.session_id}
            )
            await media.send_event({"type": "assistant.listening"})
        except BaseException:
            await self.close("startup_failed")
            raise

    def _create_processors(
        self,
        user: TrustedUserContext,
    ) -> Sequence[FrameProcessor]:
        """创建算法链；P7 可注入内存处理器，生产默认使用真实适配器。"""
        if self._processor_factory is not None:
            return self._processor_factory(user)
        from .existing_agent import ExistingAgentProcessor, ExistingAgentSSEClient

        agent_client = ExistingAgentSSEClient(
            self.settings.agent_chat_url,
            user,
            timeout_seconds=self.settings.agent_timeout_seconds,
        )
        return [
            EnergyVADProcessor(
                threshold=self.settings.vad_energy_threshold,
                start_ms=self.settings.vad_start_ms,
                stop_ms=self.settings.vad_stop_ms,
            ),
            AliyunStreamingSTTProcessor(
                self.settings.aliyun_api_key,
                self.settings.aliyun_stt_model,
                self.settings.audio_in_sample_rate,
            ),
            ExistingAgentProcessor(agent_client),
            StreamingTTSProcessor(
                CosyVoiceTTSService(
                    self.settings.aliyun_api_key,
                    self.settings.cosyvoice_model,
                    self.settings.cosyvoice_voice,
                ),
                self.settings.tts_chunk_max_chars,
            ),
        ]

    async def close(self, reason: str) -> None:
        """所有调用者等待同一次清理，保证流水线和媒体连接只关闭一次。"""
        async with self._close_lock:
            if self._close_task is None:
                self._state = "closing"
                self._close_task = asyncio.create_task(
                    self._close_resources(reason),
                    name="voice:session-close",
                )
            close_task = self._close_task
        if close_task is asyncio.current_task():
            return
        await asyncio.shield(close_task)

    async def wait_closed(self) -> None:
        """等待会话完成全部资源清理，供服务层移除注册信息。"""
        await self._closed_event.wait()

    async def _close_resources(self, reason: str) -> None:
        """尽最大努力释放每一层资源，单个清理失败不阻断后续清理。"""
        idle_task, self._idle_task = self._idle_task, None
        pipeline, self._pipeline = self._pipeline, None
        media, self._media = self._media, None
        try:
            if idle_task is not None and idle_task is not asyncio.current_task():
                idle_task.cancel()
                with suppress(asyncio.CancelledError):
                    await idle_task
            try:
                if pipeline is not None:
                    await pipeline.stop(reason)
            except Exception:
                logger.exception("关闭语音流水线失败 | reason=%s", reason)
            try:
                if media is not None:
                    await media.send_event(
                        {"type": "session.closed", "reason": reason}
                    )
            except Exception:
                logger.exception("发送语音会话关闭事件失败 | reason=%s", reason)
            try:
                if media is not None:
                    await media.close()
            except Exception:
                logger.exception("关闭语音媒体会话失败 | reason=%s", reason)
        finally:
            async with self._close_lock:
                self._state = "closed"
                self._closed_event.set()

    async def _on_audio(self, audio: bytes) -> None:
        pipeline = self._pipeline
        if pipeline is None or self._state != "running":
            return
        try:
            await pipeline.queue_frame(
                InputAudioFrame(
                    audio=audio,
                    sample_rate=self.settings.audio_in_sample_rate,
                    channels=1,
                )
            )
        except RuntimeError:
            if self._state not in {"closing", "closed"}:
                raise

    async def _handle_output(self, frame: Frame) -> None:
        media = self._media
        if media is None or self._state not in {"starting", "running"}:
            return
        if isinstance(frame, OutputAudioFrame):
            self._last_activity = time.monotonic()
            await media.send_audio(frame.audio)
        elif isinstance(frame, InterruptFrame):
            await media.clear_audio()
            await media.send_event(
                {"type": "assistant.interrupted", "turn_id": frame.turn_id}
            )
        elif isinstance(frame, StateFrame):
            await media.send_event({"type": frame.event, **dict(frame.data)})
        elif isinstance(frame, UserSpeechStartedFrame):
            self._last_activity = time.monotonic()
        elif isinstance(frame, AgentEndFrame):
            self._last_activity = time.monotonic()
            await media.send_event(
                {
                    "type": "assistant.answer.completed",
                    "turn_id": frame.turn_id,
                    "references": list(frame.references),
                }
            )
        elif isinstance(frame, ErrorFrame):
            event = {
                "type": "session.error",
                "component": frame.component,
                "message": frame.message,
                "fatal": frame.fatal,
            }
            if frame.turn_id is not None:
                event["turn_id"] = frame.turn_id
            await media.send_event(event)
            if frame.fatal:
                asyncio.create_task(self.close(f"fatal:{frame.component}"))

    async def _watch_idle(self) -> None:
        """长时间没有说话或输出时主动释放常驻云连接。"""
        interval = min(5.0, self.settings.session_idle_seconds)
        while self._state == "running":
            await asyncio.sleep(interval)
            if (
                time.monotonic() - self._last_activity
                >= self.settings.session_idle_seconds
            ):
                await self.close("idle_timeout")
                return
````

### `qll_chat/cascaded_voice_agent/session_registry.py`

````python
"""短期语音令牌和进程内会话注册表。"""

import asyncio
import secrets
import time
from dataclasses import dataclass, field

from .contracts import TrustedUserContext


@dataclass(slots=True)
class SessionRegistration:
    context: TrustedUserContext
    token: str = field(repr=False)
    expires_at: float
    connected: bool = False


class VoiceSessionRegistry:
    """第一版单进程注册表；服务重启后客户端必须重新创建会话。"""

    def __init__(self, token_ttl_seconds: float) -> None:
        if token_ttl_seconds <= 0:
            raise ValueError("语音会话令牌有效期必须大于 0")
        self._ttl = token_ttl_seconds
        self._items: dict[str, SessionRegistration] = {}
        self._lock = asyncio.Lock()

    async def create(self, context: TrustedUserContext) -> SessionRegistration:
        context.validate()
        async with self._lock:
            self._purge_expired()
            if context.voice_session_id in self._items:
                raise ValueError("voice_session_id 已存在")
            registration = SessionRegistration(
                context=context,
                token=secrets.token_urlsafe(32),
                expires_at=time.time() + self._ttl,
            )
            self._items[context.voice_session_id] = registration
            return registration

    async def authenticate(
        self,
        session_id: str,
        token: str,
        *,
        mark_connected: bool = False,
    ) -> SessionRegistration:
        if not session_id or not token:
            raise PermissionError("语音会话令牌无效或已过期")
        async with self._lock:
            self._purge_expired()
            registration = self._items.get(session_id)
            if registration is None or not secrets.compare_digest(registration.token, token):
                raise PermissionError("语音会话令牌无效或已过期")
            if mark_connected and registration.connected:
                raise ValueError("语音会话已经连接")
            if mark_connected:
                registration.connected = True
            return registration

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            self._items.pop(session_id, None)

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [
            key
            for key, value in self._items.items()
            if not value.connected and value.expires_at <= now
        ]
        for key in expired:
            self._items.pop(key, None)
````

### `qll_chat/cascaded_voice_agent/transport.py`

````python
"""基于 aiortc 的手机端一对一 WebRTC 媒体会话。"""

import asyncio
import fractions
import json
import logging
import time
from collections import deque
from contextlib import suppress
from typing import Any, Awaitable, Callable, Mapping

import numpy as np
from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError
from av import AudioFrame, AudioResampler


logger = logging.getLogger(__name__)


class RawPCMOutputTrack(AudioStreamTrack):
    """按 10 ms 节奏把 24 kHz 单声道 PCM 放入 WebRTC 音轨。"""

    def __init__(self, sample_rate: int = 24000) -> None:
        if sample_rate <= 0 or sample_rate % 100:
            raise ValueError("WebRTC 输出采样率必须是 100 的正整数倍")
        super().__init__()
        self._sample_rate = sample_rate
        self._samples_per_frame = sample_rate // 100
        self._bytes_per_frame = self._samples_per_frame * 2
        self._timestamp = 0
        self._started_at: float | None = None
        self._audio = bytearray()
        self._max_buffer_bytes = sample_rate * 2 * 5

    async def add_audio(self, audio: bytes) -> None:
        if not isinstance(audio, bytes) or len(audio) % 2:
            raise ValueError("WebRTC 输出必须是完整的 PCM16 bytes")
        self._audio.extend(audio)
        if len(self._audio) > self._max_buffer_bytes:
            # shortcut: 首版保留最近 5 秒，P9 根据真实播放积压增加指标告警。
            overflow = len(self._audio) - self._max_buffer_bytes
            del self._audio[: overflow + overflow % 2]

    async def clear(self) -> None:
        self._audio.clear()

    async def recv(self) -> AudioFrame:
        if self._started_at is None:
            self._started_at = time.monotonic()
        if self._timestamp:
            wait = self._started_at + self._timestamp / self._sample_rate - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)

        if len(self._audio) >= self._bytes_per_frame:
            chunk = bytes(self._audio[: self._bytes_per_frame])
            del self._audio[: self._bytes_per_frame]
        elif self._audio:
            chunk = bytes(self._audio).ljust(self._bytes_per_frame, b"\x00")
            self._audio.clear()
        else:
            chunk = bytes(self._bytes_per_frame)

        samples = np.frombuffer(chunk, dtype=np.int16)
        frame = AudioFrame.from_ndarray(samples[None, :], layout="mono")
        frame.sample_rate = self._sample_rate
        frame.pts = self._timestamp
        frame.time_base = fractions.Fraction(1, self._sample_rate)
        self._timestamp += self._samples_per_frame
        return frame


class WebRTCMediaSession:
    """负责 SDP、输入重采样、输出音轨和状态 DataChannel。"""

    def __init__(
        self,
        session_id: str,
        *,
        input_sample_rate: int = 16000,
        output_sample_rate: int = 24000,
        ice_servers: tuple[Mapping[str, Any], ...] = (),
    ) -> None:
        if not session_id.strip():
            raise ValueError("WebRTC session_id 不能为空")
        if input_sample_rate != 16000 or output_sample_rate != 24000:
            raise ValueError("第一版 WebRTC 固定使用 16 kHz 输入和 24 kHz 输出")
        self.session_id = session_id
        rtc_ice_servers = [
            RTCIceServer(
                urls=item.get("urls"),
                username=item.get("username"),
                credential=item.get("credential"),
            )
            for item in ice_servers
        ]
        self._pc = RTCPeerConnection(RTCConfiguration(iceServers=rtc_ice_servers))
        self._input_sample_rate = input_sample_rate
        self._output_track = RawPCMOutputTrack(output_sample_rate)
        self._pc.addTrack(self._output_track)
        self._audio_handler: Callable[[bytes], Awaitable[None]] | None = None
        self._disconnect_handler: Callable[[str], Awaitable[None]] | None = None
        self._data_channel = None
        self._pending_events: deque[str] = deque(maxlen=100)
        self._reader_tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._offer_accepted = False
        self._disconnect_notified = False
        self._install_handlers()

    def bind_audio_input(self, handler: Callable[[bytes], Awaitable[None]]) -> None:
        self._audio_handler = handler

    def bind_disconnect(self, handler: Callable[[str], Awaitable[None]]) -> None:
        self._disconnect_handler = handler

    async def accept_offer(self, sdp: str, description_type: str) -> dict[str, str]:
        if description_type != "offer":
            raise ValueError("WebRTC description type 必须是 offer")
        if self._offer_accepted:
            raise ValueError("WebRTC offer 只能提交一次")
        if not isinstance(sdp, str) or not sdp.strip():
            raise ValueError("WebRTC SDP offer 不能为空")
        if len(sdp.encode("utf-8")) > 256 * 1024:
            raise ValueError("WebRTC SDP offer 超过 256 KiB")
        self._offer_accepted = True
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp, type=description_type)
        )
        answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(answer)
        local = self._pc.localDescription
        if local is None:
            raise RuntimeError("WebRTC 未生成本地 SDP answer")
        return {"sdp": local.sdp, "type": local.type, "session_id": self.session_id}

    async def send_audio(self, audio: bytes) -> None:
        if not self._closed:
            await self._output_track.add_audio(audio)

    async def clear_audio(self) -> None:
        await self._output_track.clear()

    async def send_event(self, event: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(event), ensure_ascii=False)
        channel = self._data_channel
        if channel is not None and channel.readyState == "open":
            channel.send(payload)
        elif not self._closed:
            self._pending_events.append(payload)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        current_task = asyncio.current_task()
        tasks = tuple(task for task in self._reader_tasks if task is not current_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._reader_tasks.clear()
        self._pending_events.clear()
        self._output_track.stop()
        await self._pc.close()

    def _install_handlers(self) -> None:
        @self._pc.on("track")
        def on_track(track) -> None:
            if track.kind != "audio":
                return
            task = asyncio.create_task(
                self._read_audio(track),
                name=f"voice:webrtc-input:{self.session_id}",
            )
            self._reader_tasks.add(task)
            task.add_done_callback(self._reader_tasks.discard)

        @self._pc.on("datachannel")
        def on_datachannel(channel) -> None:
            self._data_channel = channel

            @channel.on("open")
            def on_open() -> None:
                self._flush_pending_events(channel)

            self._flush_pending_events(channel)

        @self._pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            state = self._pc.connectionState
            if (
                not self._closed
                and state in {"failed", "closed", "disconnected"}
            ):
                await self._notify_disconnect(state)

    async def _read_audio(self, track) -> None:
        resampler = AudioResampler(format="s16", layout="mono", rate=self._input_sample_rate)
        try:
            while not self._closed:
                frame = await track.recv()
                for converted in resampler.resample(frame):
                    pcm = converted.to_ndarray().astype(np.int16).tobytes()
                    if self._audio_handler is not None:
                        await self._audio_handler(pcm)
        except MediaStreamError:
            await self._notify_disconnect("audio_ended")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WebRTC 输入音轨读取失败 | session_id=%s", self.session_id)
            await self._notify_disconnect("audio_failed")

    async def _notify_disconnect(self, reason: str) -> None:
        if self._disconnect_notified or self._closed:
            return
        self._disconnect_notified = True
        if self._disconnect_handler is not None:
            await self._disconnect_handler(reason)

    def _flush_pending_events(self, channel) -> None:
        while self._pending_events and channel.readyState == "open":
            channel.send(self._pending_events.popleft())
````

### `qll_chat/cascaded_voice_agent/providers/__init__.py`

````python
"""级联语音流水线使用的云语音供应商适配器。"""

from .aliyun_stt import AliyunStreamingSTTProcessor
from .cosyvoice_tts import CosyVoiceTTSService

__all__ = ["AliyunStreamingSTTProcessor", "CosyVoiceTTSService"]
````

### `qll_chat/cascaded_voice_agent/providers/aliyun_stt.py`

````python
"""阿里云 Paraformer 实时 ASR 的话轮级 Processor 适配器。"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any

from ..frames import (
    ErrorFrame,
    Frame,
    InputAudioFrame,
    StopFrame,
    TranscriptFrame,
    UserSpeechStartedFrame,
    UserSpeechStoppedFrame,
)
from ..processor import FrameProcessor


class _RecognitionCallback:
    """收集 SDK 回调结果，并把中间转写安全送回事件循环。"""

    def __init__(
        self,
        owner: "AliyunStreamingSTTProcessor",
        turn_id: int,
        generation: int,
    ) -> None:
        self._owner = owner
        self._turn_id = turn_id
        self._generation = generation
        self._lock = threading.Lock()
        self._finalized_parts: list[str] = []
        self._partial_text = ""
        self._error_message = ""

    @property
    def error_message(self) -> str:
        with self._lock:
            return self._error_message

    def final_text(self) -> str:
        with self._lock:
            return "".join([*self._finalized_parts, self._partial_text]).strip()

    def on_open(self) -> None:
        return None

    def on_complete(self) -> None:
        return None

    def on_close(self) -> None:
        return None

    def on_event(self, result: Any) -> None:
        sentence = result.get_sentence()
        if not sentence:
            return
        sentences = sentence if isinstance(sentence, list) else [sentence]
        for item in sentences:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            is_sentence_end = bool(result.is_sentence_end(item))
            with self._lock:
                if is_sentence_end:
                    self._finalized_parts.append(text)
                    self._partial_text = ""
                else:
                    self._partial_text = text
                current_text = "".join(
                    [*self._finalized_parts, self._partial_text]
                ).strip()
            if current_text:
                self._owner.emit_from_callback(
                    TranscriptFrame(
                        turn_id=self._turn_id,
                        text=current_text,
                        is_final=False,
                    ),
                    self._generation,
                )

    def on_error(self, result: Any) -> None:
        message = str(getattr(result, "message", None) or result)
        with self._lock:
            self._error_message = message
        self._owner.emit_from_callback(
            ErrorFrame(
                component="aliyun-asr",
                message=message,
                fatal=False,
                turn_id=self._turn_id,
            ),
            self._generation,
        )


class AliyunStreamingSTTProcessor(FrameProcessor):
    """把每个 VAD 话轮转换成中间转写和唯一最终转写。"""

    _PRE_ROLL_MS = 300
    _SEND_CHUNK_MS = 100

    def __init__(self, api_key: str, model: str, sample_rate: int = 16000) -> None:
        super().__init__("aliyun-asr")
        if not str(api_key or "").strip():
            raise ValueError("阿里云 ASR 缺少 API Key")
        if not str(model or "").strip():
            raise ValueError("阿里云 ASR 缺少模型名称")
        if sample_rate != 16000:
            raise ValueError("第一版 ASR 仅接受 16 kHz 单声道 PCM16")
        self._api_key = api_key
        self._model = model
        self._sample_rate = sample_rate
        self._recognition_factory: Any = None
        self._recognition: Any = None
        self._callback: _RecognitionCallback | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._turn_id: int | None = None
        self._generation = 0
        self._send_buffer = bytearray()
        self._pre_roll: deque[bytes] = deque()
        self._pre_roll_bytes = 0
        self._callback_tasks: set[asyncio.Task[None]] = set()
        self._pre_roll_limit = self._bytes_for_ms(self._PRE_ROLL_MS)
        self._send_chunk_bytes = self._bytes_for_ms(self._SEND_CHUNK_MS)

    async def on_start(self, frame: Frame) -> None:
        import dashscope
        from dashscope.audio.asr import Recognition

        dashscope.api_key = self._api_key
        self._recognition_factory = Recognition
        self._loop = asyncio.get_running_loop()

    async def process_frame(self, frame: Frame) -> None:
        if isinstance(frame, UserSpeechStartedFrame):
            await self._start_turn(frame.turn_id)
        elif isinstance(frame, UserSpeechStoppedFrame):
            if frame.turn_id == self._turn_id:
                await self._finish_turn(emit_final=True)
        elif isinstance(frame, InputAudioFrame):
            self._validate_audio(frame)
            if self._recognition is None:
                self._remember_pre_roll(frame.audio)
            else:
                self._feed_audio(frame.audio)
        elif isinstance(frame, StopFrame):
            await self._finish_turn(emit_final=False)
        await self.push_frame(frame)

    async def on_stop(self) -> None:
        await self._finish_turn(emit_final=False)
        await self._drain_callback_tasks()
        self._loop = None

    async def _start_turn(self, turn_id: int) -> None:
        if self._recognition is not None:
            await self._finish_turn(emit_final=False)
        if self._recognition_factory is None:
            raise RuntimeError("阿里云 ASR 尚未初始化")

        self._generation += 1
        callback = _RecognitionCallback(self, turn_id, self._generation)
        recognition = self._recognition_factory(
            model=self._model,
            callback=callback,
            format="pcm",
            sample_rate=self._sample_rate,
            semantic_punctuation_enabled=False,
        )
        self._recognition = recognition
        self._callback = callback
        self._turn_id = turn_id
        start_task = asyncio.create_task(asyncio.to_thread(recognition.start))
        try:
            await asyncio.shield(start_task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(start_task)
                await asyncio.to_thread(recognition.stop)
            finally:
                self._reset_turn()
            raise
        except BaseException:
            self._reset_turn()
            raise

        # shortcut: 首版固定保留 300ms 前导音频，P9 再按手机实测校准。
        buffered = tuple(self._pre_roll)
        self._clear_pre_roll()
        for audio in buffered:
            self._feed_audio(audio)

    async def _finish_turn(self, *, emit_final: bool) -> None:
        recognition = self._recognition
        callback = self._callback
        turn_id = self._turn_id
        if recognition is None or callback is None or turn_id is None:
            return

        stop_error: Exception | None = None
        cancelled_error: asyncio.CancelledError | None = None
        if self._send_buffer:
            try:
                recognition.send_audio_frame(bytes(self._send_buffer))
            except Exception as exc:
                stop_error = exc
            finally:
                self._send_buffer.clear()

        try:
            stop_task = asyncio.create_task(asyncio.to_thread(recognition.stop))
            try:
                await asyncio.shield(stop_task)
            except asyncio.CancelledError as exc:
                cancelled_error = exc
                await asyncio.shield(stop_task)
        except Exception as exc:
            stop_error = stop_error or exc

        await self._drain_callback_tasks()
        final_text = callback.final_text()
        callback_error = callback.error_message
        self._reset_turn()

        if cancelled_error is not None:
            raise cancelled_error

        if stop_error is not None and not callback_error:
            await self.push_frame(
                ErrorFrame(
                    component="aliyun-asr",
                    message=f"语音识别收尾失败：{stop_error}",
                    fatal=False,
                    turn_id=turn_id,
                )
            )
        if emit_final and final_text and not callback_error and stop_error is None:
            await self.push_frame(
                TranscriptFrame(
                    turn_id=turn_id,
                    text=final_text,
                    is_final=True,
                )
            )

    def _feed_audio(self, audio: bytes) -> None:
        if self._recognition is None or not audio:
            return
        self._send_buffer.extend(audio)
        while len(self._send_buffer) >= self._send_chunk_bytes:
            chunk = bytes(self._send_buffer[: self._send_chunk_bytes])
            del self._send_buffer[: self._send_chunk_bytes]
            self._recognition.send_audio_frame(chunk)

    def _remember_pre_roll(self, audio: bytes) -> None:
        if not audio:
            return
        self._pre_roll.append(audio)
        self._pre_roll_bytes += len(audio)
        while self._pre_roll_bytes > self._pre_roll_limit and self._pre_roll:
            self._pre_roll_bytes -= len(self._pre_roll.popleft())

    def _clear_pre_roll(self) -> None:
        self._pre_roll.clear()
        self._pre_roll_bytes = 0

    def _reset_turn(self) -> None:
        self._recognition = None
        self._callback = None
        self._turn_id = None
        self._send_buffer.clear()
        self._generation += 1

    async def _drain_callback_tasks(self) -> None:
        await asyncio.sleep(0)
        tasks = tuple(self._callback_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def emit_from_callback(self, frame: Frame, generation: int) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(
                self._schedule_callback_frame,
                frame,
                generation,
            )

    def _schedule_callback_frame(self, frame: Frame, generation: int) -> None:
        if generation != self._generation:
            return
        task = asyncio.create_task(self.push_frame(frame))
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)

    def _bytes_for_ms(self, duration_ms: int) -> int:
        return self._sample_rate * 2 * duration_ms // 1000

    def _validate_audio(self, frame: InputAudioFrame) -> None:
        if frame.sample_rate != self._sample_rate or frame.channels != 1:
            raise ValueError("ASR 仅接受 16 kHz 单声道 PCM16")
````

### `qll_chat/cascaded_voice_agent/providers/cosyvoice_tts.py`

````python
"""CosyVoice 流式 TTS SDK 的异步封装。"""

import asyncio
import re
import threading
from concurrent.futures import Future
from importlib import metadata
from typing import Any, Awaitable, Callable


_MINIMUM_CANCEL_VERSION = (1, 26, 4)


class _TTSCallback:
    """把 SDK 工作线程里的音频和错误切回 asyncio 事件循环。"""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_audio: Callable[[bytes], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
    ) -> None:
        self._loop = loop
        self._on_audio = on_audio
        self._on_error = on_error
        self._is_active: Callable[[], bool] = lambda: True
        self._pending_audio = bytearray()
        self._pending_callbacks: set[Future[Any]] = set()
        self._lock = threading.Lock()

    def bind_activity_check(self, is_active: Callable[[], bool]) -> None:
        """绑定当前任务检查，丢弃取消后迟到的 SDK 回调。"""
        self._is_active = is_active

    def on_open(self) -> None:
        """连接成功后等待 SDK 接收文本。"""
        return None

    def on_complete(self) -> None:
        """PCM16 不应遗留半个采样；发现异常时交给上层定位。"""
        with self._lock:
            has_incomplete_sample = bool(self._pending_audio)
            self._pending_audio.clear()
        if has_incomplete_sample and self._is_active():
            self._submit(self._on_error("CosyVoice 返回了不完整的 PCM16 采样"))

    def on_close(self) -> None:
        """连接关闭不需要额外事件。"""
        return None

    def on_event(self, message: str) -> None:
        """首版不向前端暴露供应商内部事件。"""
        return None

    def on_data(self, data: bytes) -> None:
        if not self._is_active() or not data:
            return
        with self._lock:
            self._pending_audio.extend(data)
            aligned_size = len(self._pending_audio) // 2 * 2
            if not aligned_size:
                return
            audio = bytes(self._pending_audio[:aligned_size])
            del self._pending_audio[:aligned_size]
        self._submit(self._on_audio(audio))

    def on_error(self, message) -> None:
        if self._is_active():
            self._submit(self._on_error(str(message)))

    def _submit(self, coroutine) -> None:
        """事件循环关闭后安全丢弃回调，避免产生未等待协程。"""
        if self._loop.is_closed():
            coroutine.close()
            return
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError:
            coroutine.close()
            return
        with self._lock:
            self._pending_callbacks.add(future)
        future.add_done_callback(self._discard_callback)

    def _discard_callback(self, future: Future[Any]) -> None:
        with self._lock:
            self._pending_callbacks.discard(future)

    async def drain(self) -> None:
        """等待 streaming_complete 之前已提交的音频回调真正下传。"""
        while True:
            with self._lock:
                pending = tuple(self._pending_callbacks)
            if not pending:
                return
            await asyncio.gather(
                *(asyncio.wrap_future(item) for item in pending),
                return_exceptions=True,
            )

    def cancel_pending(self) -> None:
        """取消已经排入事件循环、但尚未完成的旧话轮回调。"""
        with self._lock:
            pending = tuple(self._pending_callbacks)
        for future in pending:
            future.cancel()


class CosyVoiceTTSService:
    """每个播报话轮创建一个 SDK 任务，支持完成和立即取消。"""

    def __init__(self, api_key: str, model: str, voice: str) -> None:
        if not str(api_key or "").strip():
            raise ValueError("CosyVoice 缺少 API Key")
        if not str(model or "").strip():
            raise ValueError("CosyVoice 缺少模型名称")
        if not str(voice or "").strip():
            raise ValueError("CosyVoice 缺少音色名称")
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._synthesizer: Any = None
        self._callback: _TTSCallback | None = None
        self._generation = 0
        self._state_lock = asyncio.Lock()

    async def start(
        self,
        on_audio: Callable[[bytes], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
    ) -> None:
        import dashscope
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

        self._validate_sdk_version()
        await self.cancel()
        dashscope.api_key = self._api_key
        self._generation += 1
        generation = self._generation
        callback = _TTSCallback(asyncio.get_running_loop(), on_audio, on_error)
        callback.bind_activity_check(
            lambda: generation == self._generation and self._synthesizer is not None
        )
        synthesizer = SpeechSynthesizer(
            model=self._model,
            voice=self._voice,
            format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            callback=callback,
        )
        async with self._state_lock:
            self._synthesizer = synthesizer
            self._callback = callback

    async def send_text(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("CosyVoice 文本必须是字符串")
        if not text.strip():
            return
        if self._synthesizer is None:
            raise RuntimeError("CosyVoice 任务尚未启动")
        await asyncio.to_thread(self._synthesizer.streaming_call, text)

    async def finish(self, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("CosyVoice 完成超时必须大于 0")
        async with self._state_lock:
            synthesizer = self._synthesizer
            callback = self._callback
        if synthesizer is None:
            return
        try:
            # 官方 streaming_complete 不接收超时参数，客户端超时由 asyncio 控制。
            await asyncio.wait_for(
                asyncio.to_thread(synthesizer.streaming_complete),
                timeout=timeout_seconds,
            )
            if callback is not None:
                await callback.drain()
        except TimeoutError as exc:
            await self._cancel_synthesizer(synthesizer)
            raise RuntimeError("CosyVoice 流式合成完成超时") from exc
        finally:
            async with self._state_lock:
                if self._synthesizer is synthesizer:
                    self._synthesizer = None
                    self._callback = None
                    self._generation += 1

    async def cancel(self) -> None:
        async with self._state_lock:
            synthesizer, self._synthesizer = self._synthesizer, None
            callback, self._callback = self._callback, None
            self._generation += 1
        if callback is not None:
            callback.cancel_pending()
        if synthesizer is not None:
            await self._cancel_synthesizer(synthesizer)

    async def close(self) -> None:
        await self.cancel()

    async def _cancel_synthesizer(self, synthesizer: Any) -> None:
        """等待服务端确认取消，确保旧话轮不会继续占用连接。"""
        await asyncio.to_thread(synthesizer.streaming_cancel, 10_000)

    @staticmethod
    def _validate_sdk_version() -> None:
        """取消能力要求 DashScope 1.26.4+，旧版本不能满足插话语义。"""
        try:
            raw_version = metadata.version("dashscope")
        except metadata.PackageNotFoundError:
            # 测试替身可能只注入模块；真实运行时的 import 会先暴露缺包。
            return
        numbers = tuple(int(part) for part in re.findall(r"\d+", raw_version)[:3])
        normalized = numbers + (0,) * (3 - len(numbers))
        if normalized < _MINIMUM_CANCEL_VERSION:
            required = ".".join(str(part) for part in _MINIMUM_CANCEL_VERSION)
            raise RuntimeError(
                f"CosyVoice 可中断合成要求 dashscope>={required}，"
                f"当前版本为 {raw_version}"
            )
````

### `services/cascaded_voice_agent_service.py`

````python
"""第一版自研级联语音服务：可信建会话、WebRTC 信令和资源回收。"""

import asyncio
import os
import re
import secrets
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from aiohttp import web

from project_utils.generic_utils.config_utils import load_config_tree
from qll_chat.cascaded_voice_agent import CascadedVoiceSession, TrustedUserContext
from qll_chat.cascaded_voice_agent.session_registry import VoiceSessionRegistry
from qll_chat.cascaded_voice_agent.settings import CascadedVoiceSettings


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _load_settings() -> CascadedVoiceSettings:
    env = os.getenv("CHAT_ENV", "dev")
    config_file = f"resources/application/cascaded_voice_agent/configs/{env}.conf"
    config = load_config_tree([config_file]).as_plain_ordered_dict()
    settings = CascadedVoiceSettings.from_mapping(config["cascaded_voice_agent"])
    settings.validate()
    return settings


def _bearer_token(request: web.Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise web.HTTPUnauthorized(text="缺少语音会话 Bearer Token")
    return token


def _required_text(body: Mapping[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def create_app(
    settings: CascadedVoiceSettings | None = None,
    *,
    media_factory: Callable[..., Any] | None = None,
    session_factory: Callable[[CascadedVoiceSettings], CascadedVoiceSession] = (
        CascadedVoiceSession
    ),
) -> web.Application:
    """创建 aiohttp 应用；WebRTC 长连接不放入 Flask/Gunicorn 同步 worker。"""
    settings = settings or _load_settings()
    settings.validate()
    if media_factory is None:
        from qll_chat.cascaded_voice_agent.transport import WebRTCMediaSession

        media_factory = WebRTCMediaSession
    registry = VoiceSessionRegistry(settings.session_token_ttl_seconds)
    sessions: dict[str, CascadedVoiceSession] = {}
    cleanup_tasks: set[asyncio.Task[None]] = set()

    @web.middleware
    async def error_middleware(request: web.Request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except PermissionError as exc:
            raise web.HTTPUnauthorized(text=str(exc)) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc

    app = web.Application(middlewares=[error_middleware], client_max_size=512 * 1024)

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "service": "cascaded_voice_agent"})

    async def create_voice_session(request: web.Request) -> web.Response:
        supplied_token = request.headers.get("X-Internal-Service-Token", "")
        if not secrets.compare_digest(supplied_token, settings.internal_service_token):
            raise web.HTTPUnauthorized(text="内部服务凭证无效")
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("创建语音会话的请求体必须是对象")
        session_id = body.get("session_id") or uuid.uuid4().hex
        if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("session_id 只允许 1～64 位字母、数字、下划线或连字符")
        agent_request = body.get("agent_request")
        if not isinstance(agent_request, Mapping):
            raise ValueError("agent_request 必须是可信业务后端生成的对象")
        context = TrustedUserContext(
            tenant_id=_required_text(body, "tenant_id"),
            user_id=_required_text(body, "user_id"),
            voice_session_id=session_id,
            agent_request_template=agent_request,
        )
        registration = await registry.create(context)
        return web.json_response(
            {
                "session_id": session_id,
                "voice_token": registration.token,
                "expires_at": registration.expires_at,
                "signaling_url": f"/voice/sessions/{session_id}/offer",
            },
            status=201,
        )

    async def exchange_offer(request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        token = _bearer_token(request)
        await registry.authenticate(session_id, token)
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("WebRTC offer 请求体必须是对象")
        if "ice_servers" in body:
            raise ValueError("ICE/TURN 只能由语音服务端配置")
        sdp = _required_text(body, "sdp")
        description_type = str(body.get("type", "offer"))
        registration = await registry.authenticate(
            session_id, token, mark_connected=True
        )
        media: Any = None
        voice_session: CascadedVoiceSession | None = None

        async def close_session(reason: str) -> None:
            if voice_session is not None:
                await voice_session.close(reason)
                if sessions.get(session_id) is voice_session:
                    sessions.pop(session_id, None)
            elif media is not None:
                await media.close()
            await registry.remove(session_id)

        try:
            media = media_factory(
                session_id,
                input_sample_rate=settings.audio_in_sample_rate,
                output_sample_rate=settings.audio_out_sample_rate,
                ice_servers=settings.ice_servers(),
            )
            voice_session = session_factory(settings)
            answer = await media.accept_offer(sdp, description_type)
            sessions[session_id] = voice_session
            await voice_session.run(media, registration.context)
            media.bind_disconnect(close_session)
            cleanup_task = asyncio.create_task(
                _cleanup_when_closed(session_id, voice_session),
                name=f"voice:registry-cleanup:{session_id}",
            )
            cleanup_tasks.add(cleanup_task)
            cleanup_task.add_done_callback(cleanup_tasks.discard)
            return web.json_response(answer)
        except BaseException:
            await close_session("offer_failed")
            raise

    async def _cleanup_when_closed(
        session_id: str,
        voice_session: CascadedVoiceSession,
    ) -> None:
        await voice_session.wait_closed()
        if sessions.get(session_id) is voice_session:
            sessions.pop(session_id, None)
        await registry.remove(session_id)

    async def close_voice_session(request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        await registry.authenticate(session_id, _bearer_token(request))
        voice_session = sessions.get(session_id)
        if voice_session is not None:
            await voice_session.close("client_requested")
        sessions.pop(session_id, None)
        await registry.remove(session_id)
        return web.json_response({"session_id": session_id, "closed": True})

    async def shutdown(_: web.Application) -> None:
        await asyncio.gather(
            *(session.close("server_shutdown") for session in tuple(sessions.values())),
            return_exceptions=True,
        )
        sessions.clear()
        if cleanup_tasks:
            await asyncio.gather(*tuple(cleanup_tasks), return_exceptions=True)

    app.router.add_get("/health", health)
    app.router.add_post("/voice/sessions", create_voice_session)
    app.router.add_post("/voice/sessions/{session_id}/offer", exchange_offer)
    app.router.add_delete("/voice/sessions/{session_id}", close_voice_session)
    app.on_shutdown.append(shutdown)
    return app


if __name__ == "__main__":
    _settings = _load_settings()
    app = create_app(_settings)
    web.run_app(app, host=_settings.host, port=_settings.port)
````

### `resources/application/cascaded_voice_agent/configs/default.conf`

````hocon
# 非敏感默认值；密钥必须由部署环境变量注入。
{
    "cascaded_voice_agent": {
        aliyun_api_key = ${?DASHSCOPE_API_KEY}
        aliyun_stt_model = "paraformer-realtime-v2"
        cosyvoice_model = "cosyvoice-v2"
        cosyvoice_voice = "longxiaochun_v2"
        agent_chat_url = "http://127.0.0.1:1801/chat"
        agent_chat_url = ${?VOICE_AGENT_CHAT_URL}
        internal_service_token = ${?VOICE_INTERNAL_SERVICE_TOKEN}
        host = "0.0.0.0"
        port = 8010
        agent_timeout_seconds = 120
        session_idle_seconds = 300
        session_token_ttl_seconds = 120
        tts_chunk_max_chars = 80
        vad_energy_threshold = 450
        vad_start_ms = 120
        vad_stop_ms = 600
        audio_in_sample_rate = 16000
        audio_out_sample_rate = 24000
        # ICE/TURN 只能由服务端配置，手机 offer 不能覆盖。
        webrtc_ice_urls = ${?VOICE_WEBRTC_ICE_URLS}
        webrtc_ice_username = ${?VOICE_WEBRTC_ICE_USERNAME}
        webrtc_ice_credential = ${?VOICE_WEBRTC_ICE_CREDENTIAL}
    }
}
````

### `resources/application/cascaded_voice_agent/configs/dev.conf`

````hocon
include "resources/application/cascaded_voice_agent/configs/default.conf"
````

