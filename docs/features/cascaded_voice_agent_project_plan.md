# 第一版级联语音 Agent 分阶段实施计划

> 本文是项目实施状态的唯一依据。代码文件存在不等于功能完成；只有通过当前阶段的验收门禁，状态才能改为“已完成”。

## 1. 建设原则

第一版目标仍然是：

```text
手机 WebRTC → VAD → 流式 ASR → 现有 qll_chat Agent → 流式 TTS → 手机 WebRTC
```

实施时遵守以下规则：

1. 同一时间只推进一个阶段。
2. 当前阶段没有通过代码门禁，不实现依赖它的下一阶段。真实云服务门禁若经明确决策暂缓，可以继续下一阶段代码实现，但状态只能标记为“代码完成/验收待补”，且不得进入 P7 完整链路验收或 P9 试用。
3. 每个阶段都必须明确输入、输出、改动文件、验证方式和退出标准。
4. 供应商 SDK、网络、手机等外部依赖必须用真实环境验证，不能只凭代码审查判定完成。
5. 从 P3 开始为稳定接口编写正式测试；涉及云服务、手机和网络的能力还必须补充真实联调记录，不能只依赖单元测试。
6. 超前写出的代码统一标为“草稿”，不得作为后续阶段的可靠前提。
7. 首版只做同步单轮 Agent 调用；异步多智能体在稳定链路之后单独立项。

## 2. 状态定义

| 状态 | 含义 |
|---|---|
| 占位 | 已确定接口和职责，方法尚未实现 |
| 草稿 | 有初步代码，但没有通过本阶段全部验证 |
| 进行中 | 当前唯一允许修改和验证的阶段 |
| 已完成 | 已满足退出标准，并记录验证结果 |
| 代码完成/验收待补 | 本地代码门禁通过，但真实服务或部署联调尚未通过，不等同于已完成 |
| 延后 | 不属于第一版，当前不得提前实现 |

## 3. 当前基线

### 3.1 贯穿所有阶段的门禁

以下要求不是最后统一补齐，而是从对应能力首次出现时就必须满足：

- **可观测性**：事件至少携带 `session_id`、`turn_id`、组件名和阶段耗时；不得记录原始音频、完整 SDP、密钥、令牌和完整 Agent 权限模板。
- **取消与资源释放**：每个引入后台任务、网络连接或云 SDK 的阶段，都要分别验证正常结束、异常结束和用户取消。
- **错误定位**：错误必须归属 WebRTC、VAD、ASR、Agent、TTS 或编排内核，不能只返回统一的“会话失败”。
- **配置校验**：配置在会话启动前失败，不能运行到通话中途才发现缺少密钥、地址或非法音频规格。
- **安全**：身份和权限只来自可信业务后端；手机端输入不能覆盖 tenant、user、Agent 模板或工具权限。
- **阶段记录**：每次验收记录所用环境、命令、结果和未验证项；没有证据不得把“草稿”改为“已完成”。

### 3.2 已完成

| 阶段 | 产物 | 结论 |
|---|---|---|
| P0 方案冻结 | 设计文档、ADR、首版范围 | 已完成 |
| P1 基础代码骨架 | 包目录、核心接口、模块职责和方法入口 | 已完成 |
| P2 编排内核 | FrameProcessor、VoicePipeline、生命周期 | 已完成 |
| P3 音频帧与 VAD | PCM 契约、确定性话轮事件 | 已完成 |
| P8 WebRTC 与会话安全 | aiortc、短期令牌、资源回收 | 本地代码门禁已完成 |

P4～P7 已完成代码，但真实 `/chat`、微搜、ASR 和 TTS 验收仍未完成；不能据此宣称生产链路可用。

### 3.3 代码完成但真实验收待补

下列模块已经完成本地实现，只保留真实服务验收门禁：

| 模块 | 当前状态 | 验收阶段 |
|---|---|---|
| `existing_agent.py`、`VoiceAgent` | 代码完成、真实 `/chat + 微搜` 待验收 | P4 |
| `providers/aliyun_stt.py` | 代码完成、真实 DashScope ASR 待验收 | P5 |
| `tts_chunker.py`、`tts.py`、`providers/cosyvoice_tts.py` | 代码完成、真实 CosyVoice 待验收 | P6 |
| `session.py` | 本地 10 轮门禁完成、真实完整级联待验收 | P7 |
| `transport.py`、`session_registry.py`、服务入口 | 本地双 PeerConnection 完成、部署环境待重建 | P8 |

处理原则：真实验收失败时只修复对应模块，不提前引入 P9 移动端优化或 P11 多智能体能力。

## 4. 完整实施路径

```text
P0 方案冻结
  ↓
P1 基础骨架
  ↓
P2 编排内核
  ↓
P3 音频帧与 VAD
  ↓
P4 VoiceAgent 与现有 SSE 适配
  ↓
P5 流式 ASR
  ↓
P6 流式 TTS
  ↓
P7 无 WebRTC 的完整级联会话
  ↓
P8 WebRTC 与会话安全
  ↓
P9 手机端真实链路联调
  ↓
P10 小范围试用与稳定性收口
  ↓
P11 异步多智能体升级（后续项目）
```

## 5. 各阶段任务和门禁

### P0：方案冻结

目标：确定第一版只做级联链路，并复用现有 `/chat` Agent 能力。

产物：设计文档、ADR 0003/0004，以及明确的首版非目标。

退出标准：职责边界、媒体协议、Agent 复用方式和非目标均已写入文档。

状态：已完成。

### P1：基础代码骨架

目标：只建立模块边界和方法入口，保证后续实现不会互相穿透。

涉及目录：

```text
qll_chat/cascaded_voice_agent/
├─ contracts.py
├─ frames.py
├─ processor.py
├─ pipeline.py
├─ vad.py
├─ existing_agent.py
├─ tts.py
├─ transport.py
├─ session_registry.py
├─ session.py
└─ providers/
```

验证方式：检查目录和公开类与设计一致；执行 Python 编译检查；核对模块职责使用中文说明。

退出标准：模块边界稳定，后续阶段无需重新组织目录。

状态：已完成。

### P2：编排内核

目标：先实现完全不依赖 ASR、TTS、WebRTC 和云 SDK 的异步流水线。

只允许修改：`frames.py`、`processor.py`、`pipeline.py` 和必要的 `__init__.py` 导出。

需要实现：

- Frame 数据和控制事件模型。
- Processor 输入队列、输出转发、启动和幂等停止。
- 控制 Frame 优先于普通音频 Frame。
- Pipeline 顺序连接和反向关闭。
- 处理器异常转换为可定位的错误事件。
- Pipeline 使用一次性生命周期：运行后可以幂等停止，但停止后不能重启。
- 停止开始后拒绝新的普通 Frame，避免数据落入无人消费的队列。
- 末端处理器异常也必须被 Pipeline 记录，不能因为没有下游而丢失。

验证方式：

- 用一次性内存 Processor 断言 Frame 顺序正确。
- 断言重复停止不会挂起或重复释放。
- 断言停止期间不能继续入队，停止后不能重新启动。
- 断言单个 Processor 异常能定位到组件。
- 执行包级编译检查。

退出标准：不接入任何供应商，也能证明 Frame 可以按预期流过流水线并可靠关闭。

状态：已完成（2026-10-12）。

验收记录：

- 包级 `compileall` 通过。
- `test/test_cascaded_voice_agent_pipeline.py` 的 3 个异步测试验证了控制 Frame 优先、同优先级顺序、并发幂等停止、停止后拒绝入队和停止后拒绝重启。
- 故意让末端 Callback 抛出异常，Pipeline 成功记录 `component=output` 和原始错误，并完成全链路关闭。
- 仅导入 P2 编排内核时没有加载 DashScope；高层导出改为延迟加载，原有导入方式保持不变。

### P3：音频帧与 VAD

前置条件：P2 已完成。

目标：从固定规格 PCM 输入中稳定产生开始说话、结束说话、打断和 `turn_id`。

只允许修改：`vad.py`、必要的音频 Frame 定义和配置。

需要实现：首版能量 VAD、16 kHz mono PCM16 输入约束、起音/停音判定和打断事件。

验证方式：使用程序生成的静音和正弦 PCM 做一次性断言，检查连续静音不生成话轮，一段语音只产生一组开始/结束事件。

退出标准：给定确定性 PCM 输入，事件序列可重复且没有重复话轮。真实噪声校准留到 P9。

状态：已完成（2026-10-13）。

验收记录：

- `InputAudioFrame` 在信任边界拒绝非 bytes、非 16 kHz、非单声道和不完整 PCM16 采样。
- `test/test_cascaded_voice_agent_vad.py` 使用生成的静音与 440 Hz PCM，不依赖外部音频文件。
- 5 个测试覆盖连续静音、短噪声、一次完整话轮、连续两轮 `turn_id` 和全部音频原样转发。
- `python -m unittest test.test_cascaded_voice_agent_vad -v` 全部通过。
- 包级 `compileall` 和 `git diff --check` 通过。

#### P0～P3 复核记录（2026-10-13）

- P0：设计文档、ADR 0003/0004、首版范围和非目标均存在且相互一致。
- P1：约定目录、公开模块和服务入口齐全，当前代码重新执行 `compileall` 通过。
- P2：重新验证控制 Frame 优先、同优先级顺序、并发幂等停止、停止后拒绝入队/重启，并确认核心导入不加载 DashScope。
- P3：重新执行 5 个 PCM/VAD 测试，全部通过。

结论：P0、P1、P2、P3 仍满足各自退出标准。

### P4：VoiceAgent 与现有 SSE 适配

前置条件：P3 已完成。

目标：新增独立 `VoiceAgent(BaseToolAgent)` 作为语音 LLM 层，将 ASR 最终文本映射到现有 `/chat` 请求，并解析 SSE 文本和终态。

涉及文件：`qll_chat/agents/voice_agent.py`、语音 Prompt、`chat.py` 路由、`existing_agent.py`、必要的 Agent Frame 和本设计文档。

需要实现：

- 可信 `agent_request` 模板复制与本轮问题注入。
- `functionName=voiceAgent` 路由到直接继承 `BaseToolAgent` 的独立 VoiceAgent。
- 首版只开放配套的 `search_weisou` 和 `read_weisou_document`；其他 MCP、本地工具、Skill、客户端工具和写操作全部关闭。
- aiohttp SSE 增量读取。
- 识别 `status=0/1/2/3/4/11`，只把适合播报的文本交给 TTS。
- 用户打断时取消当前 HTTP 请求。
- 保存最小文本历史，不复制 qll_chat 内部 Agent 逻辑。

验证顺序：静态导入/实例结构检查 → 本地假 SSE 端点 → 真实 `/chat` 普通问答 → `search_weisou` → 明确拒绝首版不支持的 `status=11`。

退出标准：直接调用 `/chat` 和通过适配器调用的最终语义一致。

状态：代码完成/验收待补；真实环境验收按当前决策暂缓，不阻塞后续代码实现，但阻塞 P7 完整链路验收。

已完成：

- 新增 `VoiceAgent(BaseToolAgent)`，没有继承或嵌套 `GenericAgent`。
- `/chat` 新增 `functionName=voiceAgent` 路由，模型选择和 SSE 协议继续复用现有链路。
- VoiceAgent 使用独立语音 Prompt；工具发现、请求开关和动态 provider 三处均收紧为只允许 `search_weisou` 和 `read_weisou_document`。
- 双工具过滤已下沉为无第三方依赖的 `tool_policy.py`，同时约束模型可见 schema 与执行注册表；缺少执行注册项、未知工具、写工具和畸形名称映射均 fail closed，4 个行为测试通过。
- 关闭历史 MCP 工具恢复并显式禁用本地工具，防止旧历史或运行时 provider 绕过微搜工具白名单。
- 首版沿用现有 `writer` 模型配置通道，仅通过 `functionName=voiceAgent` 建立独立路由。
- `voice_agent.py` 与 `chat.py` 编译检查通过；断言式自检确认直接继承关系、微搜配套工具过滤和无可用模块时 fail closed。
- 当前 Codex Python 缺少 `transformers`，未完成完整运行时导入与真实服务实例化。
- 可信身份在创建 HTTP 会话前校验，问题、身份和最小历史写入可信请求副本，不修改原模板。
- 适配器无条件覆盖 `functionName=voiceAgent`，旧模板或手机输入不能把语音请求降级路由到 GenericAgent。
- SSE 支持分块读取、注释保活、多事件、末尾无空行的 `[DONE]` 和非 200 错误。
- `/chat` 非 200 时只向语音链路暴露 HTTP 状态码，不再把上游响应正文带入 `session.error`，避免内部错误和业务内容泄露给手机端。
- `status=0` 与 `status=1.token` 都进入可播报文本，避免漏掉最终尾段。
- `status=1` 作为唯一终态主动关闭 SSE，不等待服务器关闭连接。
- `status=2/100` 不误报工具活动，`status=3/4` 输出工具状态。
- `status=11` 输出带 `turn_id` 的明确不支持错误和唯一兜底终态。
- 插话会取消旧 HTTP SSE，旧话轮不再输出文本、错误或终态。
- `test/test_cascaded_voice_agent_existing_agent.py` 的 9 个本地测试全部通过。

尚未完成：

- 使用真实 qll_chat `/chat` 验证 VoiceAgent 普通问答语义一致。
- 使用真实微搜查询与正文读取验证工具白名单及 `3 → 4 → 0/1` 链路。
- P4 测试夹具已改为断言固定的 `functionName=voiceAgent`；9 个本地 SSE、插话取消和历史测试通过。
- 已新增默认跳过的 `test/test_cascaded_voice_agent_live.py`：显式开启后会直连和经适配器各调用一次普通问答，再调用一次微搜；它校验协议、终态、状态顺序和工具白名单，不记录可信模板或完整答案。真实服务仍未执行，不能把测试文件存在当成验收通过。

以上真实联调仍是小范围试用前的验收门禁，但按当前决策延后补做；P5 可以先完成代码实现。

联调配置统一使用 `VOICE_AGENT_CHAT_URL`，与语音服务生产配置保持同名；不再维护仅供测试使用的第二套 `/chat` URL 变量。可信请求文件必须来自业务后端完成登录和权限校验后的真实请求，并在仓库外保存、清空历史业务内容。

现有接口定位：

- 当前 `/chat` SSE 服务入口：`services/gunicorn_chat_service.py`，经 `QAChatAPI` 进入 `qll_chat/chat.py`。仓库 dev 启动示例端口是 `1801`，private 示例是 `18001`；原先写入语音默认配置的 `8009` 实际对应没有 `/chat` 的 `services/xiaoe_agent_flask.py`，现已修正为 dev 默认 `1801` 并允许 `VOICE_AGENT_CHAT_URL` 覆盖。
- 仓库还有 `services/qa_service.py`、`services/gunicorn_qa_service.py` 和 `services/service.py` 注册 `/chat`，部署时需要确认实际启动脚本。
- 旧 `/voice_interaction` 位于 `services/voice_interaction_service.py`，调用 `VoiceInteractionAgent` 做页面语音指令分类，不提供音频 ASR，也不是本方案要复用的聊天接口。

### P5：流式 ASR

前置条件：P4 代码实现完成；真实环境验收已明确延后。

目标：把一个 VAD 话轮的 PCM 流转换为中间转写和唯一最终转写。

只允许修改：`providers/aliyun_stt.py`、ASR 配置和必要 Frame。

需要实现：DashScope Recognition 生命周期、PCM 分片发送、final transcript、取消释放和错误映射。

验证方式：必须使用真实 API Key 和真实普通话音频；SDK import 成功不算通过。

退出标准：连续完成多轮识别，每轮只有一个 final，连接能在成功、异常和取消时释放。

状态：代码完成/验收待补（2026-10-13），真实 DashScope 环境验收暂缓。

已完成：

- 每个 VAD 话轮创建独立 Recognition，避免复用已经 stop 的 SDK 对象。
- 缓存最近 300 ms 前导 PCM，避免能量 VAD 起音判定丢掉开头音节。
- 按约 100 ms、最大 3200 字节向 SDK 发送 16 kHz mono PCM16。
- SDK 回调线程通过事件循环安全回传中间转写，并用 generation 丢弃迟到回调。
- `UserSpeechStoppedFrame` 调用阻塞式 `stop` 完成收尾，汇总并且只输出一个 final transcript。
- 正常停止、会话停止和取消路径都会释放当前 Recognition；错误转换成带 `turn_id` 的 `ErrorFrame`。
- 编译检查通过；不落盘的假 SDK 断言完成连续两轮识别，每轮各一次 start/stop 和唯一 final。
- 取消路径断言确认：即使 Processor 正在等待阻塞式 stop，取消后仍会等待 SDK 关闭并清理话轮状态。
- 失败路径断言确认：尾包发送异常仍会执行 stop、输出错误，并禁止把可能截断的文本作为 final。
- `test/test_cascaded_voice_agent_aliyun_stt.py` 的 3 个异步测试已固化上述多轮、失败和取消门禁，并全部通过。

尚未完成：

- 没有使用真实 API Key 和普通话音频验证识别效果、SDK 版本兼容性与真实网络取消。
- 因真实环境验证被明确暂缓，P5 的生产验收退出标准尚未确认；进入小范围试用前必须补做。

### P6：流式 TTS

前置条件：P5 代码实现完成；P5 真实环境验收已明确暂缓，但仍阻塞 P7 完整链路验收。

目标：将 Agent 增量文本分句后转换为可中断的 24 kHz mono PCM16。

只允许修改：`tts_chunker.py`、`tts.py`、`providers/cosyvoice_tts.py`。

需要实现：中文分句、CosyVoice 流式合成、新话轮取消旧合成和带 `turn_id` 的错误事件。

验证顺序：先验证分句，再用真实 API 验证首包、连续音频和取消。

退出标准：播报中取消后不再产生旧话轮音频，所有连接被释放。

状态：代码完成/验收待补（2026-10-13）。

已完成：

- 中文标点与最大字符数分片，并拒绝非字符串输入和空文本提交。
- TTS 首个有效音频包到达时才发送 `assistant.speaking`，正常完成后恢复 `assistant.listening`。
- 输出固定使用 24 kHz mono PCM16；SDK 回调会拼接半个采样，避免向下游输出奇数字节 PCM。
- 每轮合成使用 generation 和 `turn_id` 双重隔离；取消时先向媒体层转发打断事件并使旧回调失效，再等待 SDK 释放。
- 修正 `streaming_complete()` 的调用方式：官方接口不接收超时参数，客户端使用 `asyncio.wait_for` 控制 30 秒超时。
- TTS 错误携带 `turn_id`，同一话轮的 SDK 回调错误和调用异常只向下游报告一次。
- `test/test_cascaded_voice_agent_tts.py` 与 `test/test_cascaded_voice_agent_cosyvoice_tts.py` 共 8 个测试覆盖中文分句、首音频状态、话轮隔离、PCM16 对齐、完成、取消和迟到回调，全部通过。
- 包级编译和 `git diff --check` 通过。

尚未完成：

- 已确认 `qll_chat-master/.venv` 的 site-packages 包含 `dashscope==1.27.7` 和 `aiohttp==3.14.3`；但该虚拟环境启动器无法加载标准库，且环境实际基于 Python 3.13.5，与项目的 Python 3.12.11 依赖目标不一致，因此不能作为完整运行验收环境。
- 未使用真实 API Key 验证首包延迟、连续音频、模型与音色匹配、真实网络取消及连接释放。

结论：P6 的本地代码目标已实现，目标环境版本前提已由人工确认，但真实 CosyVoice 联调尚未通过，因此仍属于“代码完成/验收待补”。可以继续 P7 本地代码实现，不能据此认定生产链路可用。

#### P0～P5 本轮复核结论（2026-10-13）

- P0：产物齐全；已修正 ADR-0004 中仍描述复用 GenericAgent/OAAgent 的过时表述，使其与独立 VoiceAgent 决策一致。
- P1：约定的模块和服务入口齐全，包级编译通过。
- P2：不落盘断言重新验证控制 Frame 优先、幂等停止、停止后拒绝入队和一次性生命周期，结果通过。
- P3：现有 5 个 PCM/VAD 测试重新执行并通过。
- P4：源码编译和固定 `functionName=voiceAgent` 的不落盘断言通过；9 个现有 SSE 测试已使用 Python 3.12.14 + 临时 aiohttp 3.14.3 重新执行并通过，但真实 `/chat + 微搜` 仍未验收。
- P5：源码编译与实现复核通过；真实 DashScope 普通话音频、SDK 版本兼容性和网络取消仍未验收。

因此，“前五个阶段均已正确完成”这一前提不成立：P0～P3 已完成，P4～P5 是代码完成/验收待补。

### P7：无 WebRTC 的完整级联会话

前置条件：P2～P6 本地代码门禁通过；按当前决策，真实 ASR、Agent 和 TTS API 联调统一后置，但仍是进入真实手机试用前的强制门禁。

目标：用内存媒体适配器贯通 `PCM → VAD → ASR → Agent → TTS → PCM`。

只允许修改：`session.py` 和装配配置；上游模块有问题时退回对应阶段修复。

需要实现：会话装配、状态事件、幂等关闭、单有效话轮、插话取消和空闲超时。

验证方式：先使用一次性内存媒体对象、真实 VAD/Agent/TTS Processor 和内存 ASR/Agent/TTS 适配器跑通，不创建测试文件；真实服务验证统一在整体代码搭建完成后补做。

本地代码退出标准：至少连续完成 10 轮，插话后旧答案不恢复，重复关闭只清理一次，空闲超时能关闭，关闭后没有遗留任务。

真实验收退出标准：替换成真实 ASR、`/chat` 和 TTS 后重复上述验证，并确认没有遗留云连接。

状态：代码完成/真实验收待补（2026-10-13）。

已完成：

- `CascadedVoiceSession` 增加一次性 `new → starting → running → closing → closed` 生命周期，关闭后不能重新启动。
- 启动失败统一进入资源清理，不遗留已绑定媒体、已启动 Pipeline 或空闲任务。
- 并发重复关闭共享同一个清理任务；Pipeline、Agent 客户端、TTS 和媒体对象只关闭一次。
- 媒体关闭回调重入 `session.close()` 时不会等待自身形成死锁。
- 空闲时间从会话成功启动时开始计算，而不是从对象构造时开始。
- 关闭期间到达的音频安全丢弃，不向已经停止的 Pipeline 入队。
- 初始输出 `session.ready → assistant.listening`；错误事件补充可选 `turn_id`。
- Agent 先下传插话再等待 HTTP 取消；TTS 使用单调话轮门槛丢弃已经排队的旧文本和旧结束帧，旧答案不会在清空后重新开播。
- 增加最小 `processor_factory` 注入点，仅用于 P7 无网络装配验证；生产默认链路仍为 VAD、阿里云 ASR、ExistingAgent、CosyVoice TTS。
- `test/test_cascaded_voice_agent_session.py` 使用真实 VAD 和 TTS Processor、内存 ASR/Agent/TTS 适配器验证连续 10 轮、三次并发关闭、空闲超时和无残留 `voice:*` 任务，2 个测试通过。
- `session.py` 将 `/chat` 网络适配器延迟到默认生产装配分支导入，保证 P7 的无网络注入验收不依赖 `aiohttp`。
- 包级编译和 `git diff --check` 通过。

尚未完成：

- 未替换为真实阿里云 ASR、真实 `/chat + 微搜` 和真实 CosyVoice 重跑 10 轮。
- 未验证真实 SDK/WebSocket/HTTP 连接在异常和取消后的服务端资源释放。

结论：P7 本地代码门禁通过，但真实 API 验收按当前决策后置；P8 本地代码也已完成，P9 手机联调前必须补齐 P4～P7 的真实服务验收和部署环境检查。

真实验收准备：`test/test_cascaded_voice_agent_live.py` 现包含三个相互独立的显式开关。`RUN_CASCADED_VOICE_LIVE=1` 验证 P4 `/chat + 微搜`；`RUN_CASCADED_VOICE_CLOUD_LIVE=1` 使用仓库外 PCM 文件验证 P5 连续两轮 ASR 各自产生唯一 final，以及 P6 TTS 两段增量输入、首音频和 PCM16 对齐；`RUN_CASCADED_VOICE_PIPELINE_LIVE=1` 以真实 VAD、ASR、VoiceAgent SSE、TTS 完成 P7 单话轮闭环并检查资源释放。全部默认跳过，当前没有真实 API 通过证据。

### P8：WebRTC 与会话安全

前置条件：P7 已完成。

目标：只把稳定的算法会话接到 aiortc，不在这一阶段修改算法语义。

涉及文件：`transport.py`、`session_registry.py`、服务入口、配置和项目依赖。

需要实现：SDP offer/answer、上下行音轨格式转换、短期单次 voice token，以及断线/超时/显式结束的资源回收。此阶段才正式加入 `aiortc` 依赖。

退出标准：本机两个 PeerConnection 能双向传输，令牌越权和重复使用被拒绝，断开后资源释放。

当前进展：

- 已实现 aiortc SDP offer/answer、16 kHz 上行重采样、24 kHz 下行音轨和 DataChannel 事件缓冲。
- 已实现短期随机 voice token、会话绑定、首次 offer 单次占用以及显式结束、断线、空闲超时的统一回收。
- ICE/TURN 只接受服务端环境配置，拒绝手机端覆盖。
- 已在项目依赖清单加入经验证的 `aiortc==1.15.0`，并同步当前环境使用的 `aiohttp==3.14.3`、`dashscope==1.27.7`。
- 本机两个 PeerConnection 已完成上行音频、下行音频和 DataChannel 双向验证；TTS 源 PCM 为 24 kHz，经过 WebRTC/Opus 协商后客户端收到 48 kHz 解码帧，属于正常的传输层重采样。
- `test/test_cascaded_voice_agent_service.py` 使用 aiohttp 内存服务覆盖内部服务鉴权、可信身份一致性、错误 token、客户端 ICE 注入、重复 offer、显式关闭和注册表回收；服务入口延迟导入默认 aiortc 媒体工厂，注入测试媒体时不会加载真实 WebRTC。该测试与 9 个 SSE 适配测试已在 Python 3.12.14 + aiohttp 3.14.3 下共 10 个通过。
- `test/test_cascaded_voice_agent_session_registry.py` 的 3 个测试覆盖错误令牌、重复连接、TTL 到期和令牌移除后不可复用。
- `test/test_cascaded_voice_agent_transport.py` 使用 `aiortc==1.15.0` 建立本机双 PeerConnection，验证上行音频、下行音频、DataChannel、重复 offer 拒绝和关闭后无残留 `voice:*` 任务；测试已真实通过，未安装 aiortc 的轻量环境会明确跳过。
- P8 本地代码退出标准已满足。部署前仍需重建 Python 3.12.11 环境并安装项目依赖：当前 `qll_chat-master/.venv` 是 Python 3.13.5，启动器已失效，且 3.13 不再提供当前 VAD 使用的 `audioop`。
- `cascaded_voice_agent_build_guide.md` 已记录 Pipecat 源码映射、完整源码清单、从零搭建、配置、协议、职责和 P4～P11 验收顺序。

### P9：手机端真实链路联调

前置条件：P8 已完成。

目标：验证真实 Android/iOS、Wi-Fi、4G/5G、HTTPS 和 TURN。

手机侧同时验证系统回声消除、降噪、自动增益、音频焦点、听筒/扬声器切换、来电/前后台切换和网络切换。首版在网络或 App 生命周期发生变化时允许结束旧会话并重建，不要求无缝迁移。

退出标准：

- 手机连续完成至少 10 轮普通话对话。
- 至少一个只读查询工具成功并播报结果。
- 播报中插话后旧音频不恢复。
- Wi-Fi 和移动网络均可建立连接。
- 扬声器播放时不会持续触发服务端 VAD 形成自问自答。
- 手机拿不到云 API Key、内部服务令牌和完整 Agent 权限模板。

### P10：小范围试用与稳定性收口

前置条件：P9 已完成。

目标：根据真实数据逐项调参，而不是同时修改多个模块。

调优顺序：WebRTC 音频规格 → VAD 阈值 → ASR → TTS 分片 → 延迟 → 错误恢复。

至少拆分记录：用户停讲到 ASR final、Agent 首文本、TTS 首音频、手机开始播放的耗时，以及插话到旧音频停止的耗时。首版先建立基线，不在没有真实样本时虚构统一阈值。

退出标准：关键失败能够定位到 WebRTC、VAD、ASR、Agent 或 TTS，并形成已知问题和升级清单。

### P11：异步多智能体升级

状态：延后，不属于第一版。

只有 P10 完成后，才设计“主语音对话继续进行、子智能体后台查询、结果回到安全话轮播报”。该阶段需要单独解决任务所有权、取消、结果时效、写操作审批、唯一终态和恢复机制。

## 6. 每一阶段的固定交付格式

每次只交付以下内容：

1. 本阶段目标和明确非目标。
2. 实际修改的文件。
3. 核心实现说明。
4. 执行过的验证命令和结果。
5. 未验证项与风险。
6. 是否满足退出标准。
7. 下一阶段的输入条件和任务清单。

没有满足第 6 项时，下一次继续处理当前阶段，不推进阶段编号。

## 7. 当前下一步

本轮已完成 P7 本地代码实现，但仍停在 P4～P7 的真实验收门禁之前。后续顺序为：

1. P8 本地代码门禁已通过；先重建 Python 3.12.11 项目环境并从依赖清单安装 `aiortc`，不要继续沿用当前失效的 Python 3.13.5 虚拟环境。
2. 整体代码搭建完成后，补做 P4 `/chat + 微搜`、P5 ASR、P6 TTS 和 P7 完整级联真实联调。
3. P4～P7 的真实门禁及 P8 部署环境检查未通过前，不进入 P9 手机端试用。

P4 联调时应先从 `services/gunicorn_chat_service.py` 的 `/chat` 入口确认实际部署服务，再通过 `VOICE_AGENT_CHAT_URL` 配置实际端口；不要把 `xiaoe_agent_flask.py` 的 `8009` 当作 `/chat`，`/voice_interaction` 也不是本方案的聊天接口。

