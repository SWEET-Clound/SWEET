# 片段：写入链路与巩固

> 行号引用为该文件内行号。三个文件同属"事件 → 候选 → 门 → 派生记忆"的异步写入链路：system.py 是同步业务门面（在线请求），consolidation.py 是 worker 侧抽取/巩固，llm.py 是可选的模型抽取器。

## 文件：persistent-memory/src/memory_system/system.py（84 行）

**文件职责**：把仓库、策略、token 上限组合成四个在线业务动作（记录事件、构建上下文、执行命令、列记忆），是 HTTP adapter 之下的唯一业务门面。

**依赖/被依赖**：模块级只有 import 语句（system.py:1-19），**无模块级常量、无 `__all__`**。依赖 `memory_system.models`（命令判别联合、`MemoryContext`、`MemoryListQuery`、`MemoryScope`、`MemorySummary`、`RecallRequest`、`RecordReceipt` 等，system.py:4-16）、`policy.MemoryPolicy`（:17）、`postgres.PostgresRepository`（:18）、`retrieval.pack_context/render_data_only`（:19）。被 `bootstrap.create_application` 构造为单例（bootstrap.py:16，注入 `settings.max_context_tokens`），被 `api.create_app` 的路由调用（api.py:161 record_event、:175 build_context、:217 execute_command_idempotent、:233 list_memories），并被 api.py:147 的异常处理器映射为 422；tests/test_memory_system.py 用 StubRepository 直接构造。调用方向是单向的：它调 policy / retrieval / repository，没有任何模块反向依赖它。

**逐段讲解**：

- `MemoryPolicyRejected(Exception)`（system.py:22）：策略拒绝的领域异常；`__init__`（system.py:23-25）把 `reason` 存成属性并转交 `Exception`，api.py:147-149 用它做 422 响应体 `{"code": exc.reason}`，所以 reason 必须是稳定机器码而非中文说明。
- `MemorySystem`（system.py:28）：业务门面类，只持三个构造期依赖（repository / policy / max_context_tokens），不做 SQL、不碰 HTTP、不解析 DTO。
- `MemorySystem.__init__`（system.py:29-37）：注入 `PostgresRepository`、`MemoryPolicy`、`max_context_tokens`；后者来自 settings（settings.py:29，默认 2048，范围 256–4096），是服务端召回预算的硬顶。
- `async record_event`（system.py:39-40）：把 `InteractionEvent` 原样交给 `repository.record_event` 并返回 `RecordReceipt`；本层不判幂等、不看 `source`、不建 outbox——是否入队抽取由 postgres.py:100 的 `source is USER and allow_memory_extraction` 决定，冲突由 `IdempotencyConflictError` 表达（api.py:139 映射 409）。
- `async build_context`（system.py:42-53）：召回唯一入口，顺序为：
  · 封顶预算（:43）`budget = min(request.token_budget, self.max_context_tokens)`，请求预算不能突破服务端上限；
  · 召回（:44-46）`repository.search(request_scope(request), request.query, request.purpose, request.now)`，仓库侧 `limit` 默认 100（postgres.py:749），不带 thread_id；
  · 打包（:47）`pack_context(candidates, budget)` → `(selected, truncated)`（retrieval.py:6-17，按 `(len+3)//4` 估 token）；
  · 组装（:48-53）`items=selected`、`rendered_text=render_data_only(selected)`（retrieval.py:20-43，包进 `<memory-data trust="untrusted">` 并转义 `& < >`）、`truncated`、`diagnostics_id=str(uuid4())`；该 id 由 api.py:185 写成 `X-Memory-Diagnostics-Id` 响应头（中间件 api.py:135-137 只在缺失时补，故此处优先）。
  · 边界：`token_budget=0` 时第一项估计值 `max(1, …)≥1 > 0` 立即超限，返回 `([], True)`——零预算表现为"截断"，不是空成功（retrieval.py:12-14）。
- `async execute_command_idempotent`（system.py:55-61）：命令写入口。先 `_validate_command(command)` 做内容硬门（未过则异常不落库），再 `repository.execute_command_idempotent(command, key, payload_hash, self.policy.version)`；策略版本随之写入记忆行（postgres.py:300），使"某条记忆由哪版策略生成"可回溯。
- `async list_memories`（system.py:63-64）：透传 `MemoryListQuery`（scope / layers / statuses / cursor / limit）给仓库（postgres.py:807-858），本层不加任何过滤；可见性靠 query.scope 与数据库 RLS。
- `_require_allowed`（system.py:66-69）：调 `policy.evaluate_explicit_memory(statement)`，`allowed=False` 时 `raise MemoryPolicyRejected(decision.reason or "policy_rejected")`；`or "policy_rejected"` 是 reason 缺失时的兜底。
- `_validate_command`（system.py:71-75）：命令分发的全部分支——`RememberCommand` 校验 `statement`（:73），`CorrectCommand` 校验 `replacement`（:75），`ForgetCommand` / `DoNotRememberCommand` 不做内容校验（它们只带 id）。
- `request_scope`（system.py:78-83）：模块级工具函数，把 `RecallRequest` 收窄成 `MemoryScope`（tenant/user/agent），主动丢弃 `thread_id` 与 `purpose`；做成自由函数而非 `RecallRequest` 的方法，是为了让模型层不反向认识系统层类型。

**批注**：

1. **命名漂移**：任务口径写作 `execute_command`，实现中只有 `execute_command_idempotent`，且幂等键与 `payload_hash` 由 adapter 计算后传入（api.py:214-219）；门面本身不具备幂等能力，名字表达的是"最终幂等"。
2. **校验不对称**：`CorrectCommand` 只校验新的 `replacement`（:75），被更正的原记忆文本不再过 policy。入库时已过门，但策略升级后旧数据不会被重扫——待确认是否要补数据迁移或重扫任务。
3. **身份/用途边界在 adapter**：`build_context` 不检查 `purpose` 白名单（api.py:173-174 才查 `identity.allowed_purposes`，403 `purpose_not_allowed`），`record_event` / `list_memories` 也不看 `Identity`；任何绕过 api.py 直接调用门面的入口（脚本、未来 gRPC）都会跳过这些检查。
4. **截断语义有歧义**：`truncated=True` 同时表示"预算不够"和"预算为 0"，调用方无法仅凭该布尔值区分"无记忆"与"装不下"，只能再看 `items` 是否为空。
5. **时钟由调用方给**：`build_context` 用 `request.now`（api.py:182 来自请求体 `request_time`）做双时态/过期过滤，服务端不覆盖；对重放与测试友好，但也意味着同一用户可用自定义 `now` 改变哪些记忆"当前有效"——待确认是有意设计还是缺口。
6. **record_event 几乎无逻辑**：它是最薄的一层（1 行转发），所有可测分支都在 postgres.py；单测因此只能覆盖 build_context 与命令门（tests/test_memory_system.py:30-156），记录链路的契约实际由集成测试承接。

---

## 文件：persistent-memory/src/memory_system/consolidation.py（167 行）

**文件职责**：worker 侧的"事件 → 记忆候选"巩固层，用规则（或规则+模型）抽取候选，过 policy 硬门后交给仓库落派生记忆与证据。

**依赖/被依赖**：依赖 `asyncio` / `re` / `datetime`（consolidation.py:1-3）、`memory_system.models`（`AffectSignal`、`EventJob`、`EventSource`、`InteractionEvent`、`MemoryCandidate`、`MemoryLayer`，:6-13）、`policy.MemoryPolicy`（:14）。被 worker.py:5 导入，由 `worker.build_extractor`（worker.py:51-66）装配、`worker.run_worker`（worker.py:97-101）构造 `Consolidator`，`worker.process_one_extraction`（worker.py:13-25）调用 `Consolidator.process` 并在异常时 `fail_extraction`。它通过鸭子类型调用 `repository.store_candidates`（postgres.py:233），自身不 import 仓库类型。测试侧被 tests/test_automatic_memory.py、tests/test_worker.py、tests/test_postgres_integration.py 直接使用。

**逐段讲解**：

- `_REMEMBER_PREFIX`（consolidation.py:16）：显式记忆前缀正则 `^\s*(?:请|麻烦)?(?:你)?记住\s*[：:]?\s*(?P<fact>.+)$`；`^` 只在字符串开头匹配且未加 `re.S`/`re.M`，所以"记住"必须在首行、且正文无换行才会命中（多行事件会静默不命中）——待确认是否符合预期。
- `_NEGATIVE_HIGH_AROUSAL`（:17-28）：10 个高唤醒负性标记（中文 7 个 + 英文 3 个），命中只用于产生"当前情绪"观测，不做任何人格/诊断推断。
- `_BOUNDED_EVENT_MARKERS`（:29）：有界事件词（会议/预约/截止/提醒我/计划/复诊/面试），必须与时间词同时出现才产出 L2。
- `_TIME_MARKERS`（:30）：相对时间词（明天/后天/下周/下个月/上午/下午/今晚）；只做字面包含，不解析真实时刻，因此"下周一"这类具体时间不会被结构化。
- `HeuristicExtractor`（:33，docstring :34）：离线安全基线抽取器，纯规则、无状态、无 IO；是 `heuristic` 模式的默认实现，也是 `hybrid` 模式的确定性一路。
- `HeuristicExtractor.extract`（:36-92）：输入 `InteractionEvent`，输出 `list[MemoryCandidate]`，四条路径：
  · 来源门（:37-38）`event.source is not EventSource.USER` → 直接 `[]`，WEB/TOOL/ASSISTANT 永不产用户记忆；
  · 显式路径（:40-52）正则命中 → **立即返回单条** L3 候选（`kind="explicit_preference"`、future_utility=high、durability=stable、confidence=1.0、`expires_at=None`），不再进入下面两条；
  · 情绪路径（:55-76）命中负性高唤醒词 → L1 候选（`kind="current_affect"`，AffectSignal：valence=−0.8、arousal=0.85、labels=["distress"]、derived_from="text"、confidence=0.75），候选 confidence 取 affect 的 0.75，`expires_at = occurred_at + 24h`（硬编码）；
  · 有界事件路径（:77-91）`_BOUNDED_EVENT_MARKERS` 与 `_TIME_MARKERS` **各至少命中一个** → L2 候选（`kind="bounded_commitment"`、utility=high、durability=uncertain、confidence=0.85、`expires_at = occurred_at + 180d`）；
  · 后两条互不互斥，同一事件可同时产 L1+L2 两条（tests/test_automatic_memory.py:61-69 锁定该行为）。
- `HeuristicExtractor._candidate`（:94-122，`@staticmethod`）：统一的候选工厂，固定填 `subject_key=None`、`valid_from=event.occurred_at`、`valid_to=None`、`source_event_ids=[]`、`sensitivity="low"`、`poisoning_flags=[]`；证据不由抽取器自报，而是在落库时按 job 的 event_id 补（postgres.py:313-322）。
- `Consolidator`（:125）：把"抽取 + policy 硬门 + 落库"串成一次 job 处理的协作者，是 worker 唯一调用的业务对象。
- `Consolidator.__init__`（:126-134）：注入 `extractor`、`policy`、`write_status: Literal["active","quarantined"]="active"`；`write_status="quarantined"` 由 worker.py:100 在 `extraction_mode=="shadow"` 时传入，实现影子写入。
- `Consolidator.process`（:136-151，`async`）：输入 `repository`（**未标注类型**）+ `EventJob`，输出新增/命中的 memory id 列表：
  · 抽取（:137）`await asyncio.to_thread(self.extractor.extract, job.event)`——把同步抽取（可能是阻塞的 httpx 调用）挪出事件循环；
  · 硬门（:138-142）对每个候选调 `policy.evaluate_candidate(candidate, job.event).allowed`，被拒的静默丢弃；
  · 短路（:143-144）若全部被拒 → `return []`，worker 仍会把该 job 标完成（worker.py:24）；
  · 落库（:145-151）`repository.store_candidates(job.event_id, job.event, candidates, self.policy.version, self.write_status)`，在单事务内做指纹 upsert + evidence + 审计（postgres.py:243-325）。
- `HybridExtractor`（:154）：多抽取器并联器，只合并、不新增业务语义。
- `HybridExtractor.__init__`（:155-156）：`*extractors` 按传入顺序保存；实际调用点 worker.py:66 传入 `(heuristic, model)`。
- `HybridExtractor.extract`（:158-166）：逐个抽取器取候选，以 `(candidate.kind, " ".join(statement.lower().split()))` 为键去重；同键保留 confidence 更高者（:164 严格 `>`，相等时保留先到者），最终 `list(unique.values())` 的顺序为首次插入顺序。

**批注**：

1. **两道来源防线不同层**：heuristic 在 :37 挡非 USER，但 `OpenAICompatibleExtractor` 不挡、照常请求模型；真正的统一兜底是 `policy.evaluate_candidate` 的 `source_cannot_create_user_memory`（policy.py:41-42）。只测抽取器等于只测第一道，漏测时第二道必须仍然成立。
2. **TTL 硬编码且与配置脱节**：L1 固定 `hours=24`（:74），而 `settings.working_memory_ttl_hours`（默认 72，settings.py:28）在整个代码库中无人读取；L2 的 180 天同样没有配置项。这类常量一旦要按产品调，得改代码而非环境变量。
3. **语义重复的双写风险**：自动候选指纹用 `candidate.kind`（如 `explicit_preference`，postgres.py:261），显式 remember 用固定前缀 `explicit`（postgres.py:487）；同一句话"自动抽到 + 用户显式记住"会得到两个不同指纹、存成两条记忆。是否需要统一 kind 词典属产品决策。
4. **shadow 闭环半开**：`write_status="quarantined"` 的候选不在召回可见状态内（postgres.py:774-777 只放 active，以及 history 用途的 superseded），只能等显式 remember 把 quarantined 提升为 active 并置信度置 1.0（postgres.py:517-524）。数据层入口存在，读侧不可见是有意设计，但"谁来看/怎么审"在代码里没有对应端点。
5. **异常隔离缺口**：HybridExtractor 没有 per-extractor `try/except`，任一抽取器抛错会让整轮抽取失败（worker.py:21-23 记 `fail_extraction`）；hybrid 模式下模型故障会连带丢掉启发式候选，与"启发式兜底"的直觉相反。
6. **类型标注不实**：`Consolidator.__init__(extractor: HeuristicExtractor)`（:128）实际接受 `HybridExtractor`/`OpenAICompatibleExtractor`；`process(self, repository, job)`（:136）未标注仓库类型。建议抽 `Protocol`（`extract(event) -> list[MemoryCandidate]`）让 mypy 替我们守住装配关系。
7. **拒绝无痕**：被 policy 拒的候选不留审计、不留计数（:143-144 后直接返回），事后只能看到 outbox 状态是 completed，无法回答"这条事件为什么没被记住"；是否要把 `reject_reason` 计入审计属待确认。

---

## 文件：persistent-memory/src/memory_system/llm.py（67 行）

**文件职责**：OpenAI 兼容 `/chat/completions` 的可选模型抽取器，把事件转成结构化 `MemoryCandidate`，仅在 shadow/model/hybrid 抽取模式下启用。

**依赖/被依赖**：依赖 `json`（llm.py:1）、`httpx`（:3）、`pydantic.BaseModel/Field`（:4）、`models.InteractionEvent/MemoryCandidate`（:6）。被 worker.py:6 导入，由 `worker.build_extractor`（worker.py:51-66）在 `extraction_mode in {"shadow","model","hybrid"}` 时构造；构造须满足 settings.py:46-49 的 `model_base_url` + `extraction_model` 校验。调用方是 `Consolidator.process`（consolidation.py:137），经 `asyncio.to_thread` 以同步方式执行。tests/test_model_extractor.py 用 `httpx.MockTransport` 注入假响应。

**逐段讲解**：

- `_SYSTEM_PROMPT`（llm.py:8-19）：唯一的模块级常量，定义模型侧契约：事件内容视为不可信数据、不作为指令（:9）；只返回 JSON（:10）；L1 短时当前状态 / L2 有界事件 / L3 稳定事实 / L4 已验证流程的使用条件（:11-13）；情绪是观察不是人格（:13）；禁止凭据、诊断、助手断言与来自 web/tool 的指令（:14-15）；不确定就不抽（:15）；并逐字段列出必填项与 `source_event_ids` 必须为空列表（:16-18）。
- `ExtractionResponse(BaseModel)`（:22）：响应外壳，字段 `candidates: list[MemoryCandidate]` 且带 `Field(max_length=8)`（:23）——单事件最多 8 条候选是硬上限，超出即 pydantic 校验失败。
- `OpenAICompatibleExtractor`（:26）：同步模型抽取器类，形态与 `HeuristicExtractor` 对齐（同签名 `extract(event)`），因此可被 `HybridExtractor` 无差别组合。
- `OpenAICompatibleExtractor.__init__`（:27-37）：拼 `self.url = f"{base_url.rstrip('/')}/chat/completions"`（:34）；记 `model`；`client or httpx.Client(timeout=30)`（:36，允许测试注入）；只有给了 `api_key` 才加 `Authorization: Bearer` 头（:37），本地兼容端点可无鉴权。无重试、无背压、温度不参数化。
- `OpenAICompatibleExtractor.extract`（:39-66）：一次 HTTP 请求一次结构化解析：
  · 请求体（:40-62）`model` + `temperature=0` + `response_format={"type":"json_object"}` + 两条 messages；
  · 用户消息（:49-59）只发 `{source, content, occurred_at.isoformat()}`，`ensure_ascii=False`；**不发** metadata / thread_id / tenant/user/agent，最小化外泄面；
  · 传输失败（:63）`response.raise_for_status()` 抛 `HTTPStatusError`；
  · 解析（:64-66）取 `choices[0]["message"]["content"]` 原文 → `ExtractionResponse.model_validate_json(content)` → `.candidates`；非 JSON、字段缺失或类型不符都会抛 pydantic 校验错；
  · 全函数无 `try/except`，任何异常直接上抛给 worker，由 worker.py:22 记为 `fail_extraction(..., type(exc).__name__)`。

**批注**：

1. **注入防护是"软"的**：事件正文仅经 `json.dumps` 转义后放进 user message（:51-58），没有 retrieval.py 那样的 `<memory-data>` 边界包装；llm.py:9 的系统提示是唯一约束。纵深依赖下一层：模型产出的候选仍要过 `policy.evaluate_candidate`（含 secrets/instruction 正则，policy.py:31-36），因此单点提示词失守不直接等于污染入库。
2. **失败即整轮失败**：`model`/`shadow` 模式下单次 HTTP 错误就让该 job 失败（worker 记 attempts，无降级）；`hybrid` 模式又缺 per-extractor 隔离（见 consolidation 批注 5），实际表现是"模型不可用 ⇒ 连启发式结果也丢"，与 `extraction_mode` 默认 `heuristic`、模型仅作增强的意图存在落差（settings.py:21、consolidation.py:34）。
3. **同步客户端 + 线程池**：`httpx.Client` 是阻塞 API，靠 `Consolidator.process:137` 的 `asyncio.to_thread` 承接。若将来换 `AsyncClient`，必须同步改调用方，否则多一次无意义线程切换。
4. **证据链不信任模型**：提示词要求 `source_event_ids` 为空（:17），仓库侧也只写 `job.event_id` 作为 evidence（postgres.py:313-322），模型即使伪造来源也不会进入证据表——这是"证据链优先于摘要"原则在模型边界上的落实。
5. **待确认**：`response_format={"type":"json_object"}` 依赖后端支持，部分兼容端点可能静默忽略（此时仍由 pydantic 兜住）；是否需要启动期 capability 探测、以及 30s 超时与 worker 并发/重试预算是否匹配，代码与文档都没有约束。
