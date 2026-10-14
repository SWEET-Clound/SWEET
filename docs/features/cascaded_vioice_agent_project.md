# 级联语音 Agent 项目交接

> 更新时间：2026-10-14  
> 工作区：`D:\APP date\python\qll_chat`  
> 文件名中的 `vioice` 按用户指定保留。  
> 本文用于新会话接手；详细设计、实施状态和完整代码分别以本文链接的三个文档为准。

## 1. 当前目标

先完成第一版面向手机的级联语音 Agent：

```text
手机 WebRTC
  -> 服务端 PCM 音频
  -> VAD
  -> 流式 ASR
  -> VoiceAgent /chat SSE
  -> 流式 TTS
  -> WebRTC 音频和状态事件返回手机
```

核心约束：

1. 编排层自研，参考 Pipecat 的 Frame、Processor、Pipeline 和取消语义，不直接依赖 Pipecat 运行时。
2. 中间 LLM 层复用 qll_chat 现有框架，但 `VoiceAgent` 直接继承 `BaseToolAgent`，不继承或嵌套 `GenericAgent`。
3. 第一版只开放 `search_weisou` 和 `read_weisou_document` 两个只读工具。
4. 第一版以持续语音对话和查询链路打通为目标；异步多智能体属于 P11，暂不实现。
5. 前端和业务后端只做职责与协议规划；当前主要交付算法端和语音服务端代码。
6. 首版调用云 API，不做私有模型部署。
7. 当前依赖策略以实际导入、函数调用和测试结果为准。允许使用兼容的新版本依赖，不能仅因版本号偏离 `requirements_py3.12.11.in` 就判定环境不可用。

## 2. 必读资料

- [详细设计](cascaded_voice_agent_design.md)
- [P0～P11 实施计划与阶段证据](cascaded_voice_agent_project_plan.md)
- [从零搭建、配置、协议和完整代码](cascaded_voice_agent_build_guide.md)
- [功能文档索引](README.md)

注意：现有实施计划中仍有“必须重建 Python 3.12.11、严格使用锁定版本”的旧表述。用户在 2026-10-14 明确修正为“新版本依赖可以使用，以实际兼容性为准”。接手后应基于真实运行结果更新旧表述，不要恢复仅按版本号判错的判断方式。

## 3. 已确认的设计决策

### 3.1 算法与会话

- 音频输入契约：首版统一为 16 kHz、单声道、16-bit little-endian PCM。
- VAD 负责确定话轮开始/结束；ASR 每轮只能产生一个 final transcript。
- Agent 文本可以增量进入 TTS；TTS 首个有效音频包到达后才进入 `assistant.speaking`。
- 用户插话必须取消旧 Agent/TTS 话轮，清空待播放音频，旧话轮的迟到回调不得恢复播报。
- 同一会话只允许一个有效话轮；关闭、超时和断线清理必须幂等。

### 3.2 Agent 与工具

- 实现文件：`qll_chat/agents/voice_agent.py`。
- 继承关系目标：`VoiceAgent(BaseToolAgent)`。
- `/chat` 入口通过固定的 `functionName=voiceAgent` 路由。
- 工具白名单由 `qll_chat/cascaded_voice_agent/tool_policy.py` 同时过滤模型 schema 和执行注册表；未知工具、写工具以及只有 schema 没有执行器的工具必须 Fail Closed。
- 身份、tenant、Agent 模板和工具权限只能来自可信业务后端，手机端不能覆盖。

### 3.3 传输与手机端

- 首版传输使用 WebRTC；ICE/TURN 由服务端配置，客户端不能注入。
- voice token 短期、单次使用，并绑定可信用户上下文。
- 手机端首版只需处理监听、思考、工具运行、播报、打断和错误状态；字幕、引用卡片和办公结果展示后续完善。
- `assistant.answer.completed` 只表示 Agent 文本结束；带当前 `turn_id` 的后续 `assistant.listening` 才表示 TTS 播放链路完成并恢复监听。

## 4. P0～P11 当前状态

| 阶段 | 当前状态 | 已有证据 / 剩余门禁 |
|---|---|---|
| P0 方案冻结 | 已完成 | 设计文档、ADR、范围和非目标已建立 |
| P1 基础骨架 | 已完成 | 包结构、公开接口、服务入口齐全 |
| P2 编排内核 | 已完成 | 优先队列、错误传播、幂等停止和不可复用生命周期已验证 |
| P3 音频帧与 VAD | 已完成 | PCM 契约和确定性 VAD 测试已通过 |
| P4 VoiceAgent 与 SSE | 代码完成，真实验收待补 | 还需真实 `/chat + 微搜`，对比直连和适配器输出及 `3 -> 4 -> 0/1` 状态序列 |
| P5 流式 ASR | 代码完成，真实验收待补 | 还需真实 DashScope 连续两轮、每轮唯一 final 和资源释放 |
| P6 流式 TTS | 代码完成，真实验收待补 | 还需真实 CosyVoice 增量输入、首音频、PCM16 对齐和完成 |
| P7 无 WebRTC 完整级联 | 本地代码门禁完成，真实验收待补 | 内存链路 10 轮、插话、超时、幂等关闭已验证；真实 VAD/ASR/Agent/TTS 待补 |
| P8 WebRTC 与安全 | 本地代码门禁完成 | 本机双 PeerConnection、DataChannel、令牌安全和资源回收已验证；当前解释器需重新实测 |
| P9 手机真实链路 | 未开始 | P4～P8 真实门禁通过后验证 Wi-Fi、4G/5G、TURN、AEC、音频焦点和网络切换 |
| P10 小范围试用 | 未开始 | 记录分段延迟、失败归因和真实调参数据 |
| P11 异步多智能体 | 明确延后 | 不属于第一版 |

不得把“代码存在”或“模拟测试通过”写成真实服务验收通过。

## 5. 主要实现位置

### 5.1 编排与算法

- `qll_chat/cascaded_voice_agent/frames.py`：音频、文本、状态、生命周期 Frame。
- `qll_chat/cascaded_voice_agent/processor.py`：Processor 基类和队列循环。
- `qll_chat/cascaded_voice_agent/pipeline.py`：处理链装配、优先级、错误和停止。
- `qll_chat/cascaded_voice_agent/vad.py`：基于 PCM RMS 的首版 VAD。
- `qll_chat/cascaded_voice_agent/providers/aliyun_stt.py`：DashScope 流式 ASR 适配器。
- `qll_chat/cascaded_voice_agent/existing_agent.py`：现有 `/chat` SSE 适配、历史、工具状态和取消。
- `qll_chat/cascaded_voice_agent/tts_chunker.py`：中文增量分句。
- `qll_chat/cascaded_voice_agent/providers/cosyvoice_tts.py`：CosyVoice 流式合成。
- `qll_chat/cascaded_voice_agent/tts.py`：TTS Processor 和话轮隔离。
- `qll_chat/cascaded_voice_agent/session.py`：完整会话装配。

### 5.2 Agent、安全和传输

- `qll_chat/agents/voice_agent.py`：语音 LLM Agent。
- `qll_chat/cascaded_voice_agent/tool_policy.py`：两个微搜工具的唯一白名单边界。
- `qll_chat/cascaded_voice_agent/transport.py`：aiortc、重采样、下行音轨和 DataChannel。
- `qll_chat/cascaded_voice_agent/session_registry.py`：短期单次令牌和会话注册。
- `qll_chat/cascaded_voice_agent/settings.py`：配置加载和启动校验。
- `services/cascaded_voice_agent_service.py`：HTTP/WebRTC 服务入口。
- `resources/application/cascaded_voice_agent/configs/`：默认和开发配置。
- `resources/common_data/prompt_template/voice_agent.md`：语音回答约束。
- `qll_chat/chat.py`：`voiceAgent` 路由接入。

## 6. 测试现状

本对话中用户已明确授权编写、修改和运行测试文件。

已有测试：

- `test/test_cascaded_voice_agent_pipeline.py`
- `test/test_cascaded_voice_agent_vad.py`
- `test/test_cascaded_voice_agent_aliyun_stt.py`
- `test/test_cascaded_voice_agent_existing_agent.py`
- `test/test_cascaded_voice_agent_tts.py`
- `test/test_cascaded_voice_agent_cosyvoice_tts.py`
- `test/test_cascaded_voice_agent_session.py`
- `test/test_cascaded_voice_agent_settings.py`
- `test/test_cascaded_voice_agent_session_registry.py`
- `test/test_cascaded_voice_agent_service.py`
- `test/test_cascaded_voice_agent_transport.py`
- `test/test_cascaded_voice_agent_tool_policy.py`
- `test/test_cascaded_voice_agent_live.py`

此前轻量回归曾得到 `Ran 37 tests ... OK (skipped=5)`；P8 的本机双 PeerConnection 也曾单独真实通过。由于解释器和依赖后来发生变化，这些结果只能证明当时状态，接手后必须在当前 `.venv` 重跑。

真实测试默认跳过：

- `RUN_CASCADED_VOICE_LIVE=1`：P4 `/chat + 微搜`。
- `RUN_CASCADED_VOICE_CLOUD_LIVE=1`：P5/P6 DashScope ASR 与 CosyVoice TTS。
- `RUN_CASCADED_VOICE_PIPELINE_LIVE=1`：P7 真实完整级联。

## 7. 当前环境诊断：不要跨解释器下结论

用户当前指定解释器：

```text
D:\APP date\python\qll_chat\.venv\Scripts\python.exe
```

2026-10-14 最近一次命令行检查观察到：

- Python 3.13.5。
- `dashscope==1.27.7`、`aiohttp==3.14.3`、`aiortc==1.15.0` 已安装。
- `pip check` 返回 `No broken requirements found`。
- `site-packages` 中存在 `audioopy==1.0`，但在上述指定解释器中执行 `import audioop` 仍返回 `ModuleNotFoundError`。
- 用户报告其本地环境中 `audioop` 已能导入并且函数可调用。该差异尚未解释，不能直接断言用户错误。
- `D:\APP date\python\xiaoe\python.exe` 是 Python 3.12.9，其中标准库 `audioop` 可导入；此前一次测试误用了这个解释器，导致 DashScope 版本被报告为 1.25.0。不要再混用两个环境的结论。
- 指定 qll_chat `.venv` 中导入 `VoiceAgent` 时，当前在 `from elasticsearch.connection import create_ssl_context` 失败；安装的 Elasticsearch 为 9.5.0。新版本依赖允许使用，但必须确认代码对新版 API 的兼容性，不能仅看 `pip check`。

首先让用户在其确认可用的同一终端或 IDE Console 执行，并记录完整输出：

```python
import sys
import audioop

pcm = (1000).to_bytes(2, "little", signed=True) * 160
print(sys.executable)
print(sys.version)
print(audioop)
print(audioop.rms(pcm, 2))
```

如果 `sys.executable` 不是 qll_chat `.venv`，先统一解释器。如果一致，则比较该会话与命令行的 `sys.path`，查清 IDE 注入或未重启的旧进程。不要先降级全部依赖，也不要仅为通过测试而改业务代码。

## 8. 下一步执行顺序

### Step 1：确认当前运行时

使用当前 `.venv` 完成以下紧反馈检查，完成标准是四项全部真实通过：

```powershell
$python = "D:\APP date\python\qll_chat\.venv\Scripts\python.exe"
& $python -c "import audioop; pcm=(1000).to_bytes(2,'little',signed=True)*160; assert audioop.rms(pcm,2)==1000; print('audioop ok')"
& $python -c "import aiortc, dashscope, aiohttp; print('voice dependencies ok')"
& $python -c "from qll_chat.agents.voice_agent import VoiceAgent; assert VoiceAgent.__mro__[1].__name__ == 'BaseToolAgent'; print('VoiceAgent ok')"
& $python -m pip check
```

若失败，只修复真实失败的兼容点。新版本依赖本身不是缺陷。

### Step 2：重跑本地门禁

```powershell
& $python -m unittest discover -s test -p "test_cascaded_voice_agent*.py" -v
```

完成标准：除三个显式真实联调开关控制的用例外，本地测试全部通过；失败必须按 WebRTC、VAD、ASR、Agent、TTS 或会话层归因。

### Step 3：补 P4～P7 真实验收

准备以下外部输入，均不得提交仓库：

1. 可访问的真实 `/chat` URL，配置到 `VOICE_AGENT_CHAT_URL`；本地开发入口通常是 `services/gunicorn_chat_service.py` 的 1801 端口，不是 `xiaoe_agent_flask.py` 的 8009。
2. 从已登录业务后端获得的可信 `agent_request` JSON；清除历史业务内容，但保留权限和租户字段。
3. 一个已配置的只读微搜工具权限。
4. 16 kHz、单声道、PCM16LE、无 WAV 头的普通话音频文件。
5. DashScope API Key。只通过环境变量传入，不打印、不写入文档和测试夹具。

按 P4、P5/P6、P7 三组开关分别执行，不要一次同时排查整条链路。真实云服务会产生调用费用。

### Step 4：进入 P9 手机联调

只有 P4～P8 当前环境门禁通过后才开始。至少覆盖：

- Wi-Fi 与 4G/5G。
- TURN 中继。
- 系统 AEC、降噪、AGC 和扬声器回采。
- 前后台、来电、音频焦点和网络切换。
- 连续 10 轮、一次微搜成功播报、插话后旧音频不恢复。

## 9. Git 与工作区注意事项

- 当前语音实现、文档和多数测试仍是未跟踪文件，不能把“未提交”误判为“不存在”。
- `qll_chat/chat.py`、`requirements_py3.12.11.in` 和 `docs/features/README.md` 有本项目改动。
- `test/test_mongo/test_log_backup.py` 是用户已有的无关改动，不得修改、还原或纳入本项目提交。
- 不执行 `git reset --hard`、`git checkout --` 或清理整个工作区。
- 代码注释使用中文。

## 10. 接手完成条件

第一版只有同时满足以下条件才可标记完成：

1. 当前实际解释器能导入并运行 VAD、ASR、VoiceAgent、TTS 和 aiortc。
2. 所有非真实联调测试通过。
3. P4 真实 `/chat + 微搜` 通过，并人工确认直连与适配器答案语义一致。
4. P5 连续 ASR 话轮、P6 增量 TTS、P7 完整真实级联通过。
5. P8 在当前部署环境通过 WebRTC、令牌和资源回收复核。
6. P9 手机完成至少 10 轮并覆盖移动网络、TURN、插话和回声场景。
7. P10 形成延迟基线、失败归因和首轮已知问题清单。
8. P11 保持延后，不把异步多智能体混入第一版验收。

若任何真实门禁缺少证据，状态只能写“代码完成/验收待补”，不能写“生产可用”。
