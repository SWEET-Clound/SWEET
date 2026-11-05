# 片段：领域模型 / 策略 / 检索 / 配置

## 文件：persistent-memory/src/memory_system/models.py（273 行）
**文件职责**：整个服务的领域 DTO 与 HTTP 请求/响应 schema 的唯一声明处——从 L0 事件、召回请求、上下文、回执，到四个记忆命令，全部只在这里定义形状。
**依赖/被依赖**：只 import 标准库（`json`/`datetime`/`enum.StrEnum`/`typing`）与 `pydantic`，不依赖项目内任何模块（叶子模块）；反向被 `api.py`、`postgres.py`、`system.py`、`consolidation.py`、`llm.py`、`policy.py`、`retrieval.py`、`__init__.py` 及全部测试导入。
**逐段讲解**：
- `from __future__ import annotations`（models.py:1）：全文件注解变成字符串，`MemorySummary` 才能在第 127 行提前引用第 143 行才定义的 `AffectSignal`。
- `_bounded_metadata`（models.py:11）：Pydantic `AfterValidator` 校验函数，把 dict 用紧凑 JSON 序列化后按 **UTF-8 字节数**判断，超 32768 抛 `ValueError("metadata exceeds 32 KiB")`；输入输出都是原 dict，属于"校验后放行"。
- `BoundedMetadata`（models.py:17）：`Annotated[dict[str, Any], AfterValidator(_bounded_metadata)]` 类型别名，供 `InteractionEvent.metadata` 与 `EventRequest.metadata` 共用同一上限，避免两处上限漂移。
- `EventSource`（models.py:20）：来源枚举 `user/assistant/tool/web/admin`；它是"来源能否产生用户记忆"的判定输入（policy 硬门、consolidation 抽取前置、postgres 是否写 outbox 都读它）。
- `MemoryLayer`（models.py:28）：L1–L4 枚举，值分别是 `"L1".."L4"`（WORKING/EPISODIC/SEMANTIC/PROCEDURAL）；故意没有 L0——原始事件不是记忆。数据库里落的是 `.value` 字符串（如 postgres.py:297），读回时 `MemoryLayer(row["layer"])`。
- `Identity`（models.py:35）：JWT 解析后的**可信身份**：tenant/user/agent（各 1..128 字符）、`allowed_purposes: set[str]`（默认空集）、`memory_write: bool`（默认 False）。只有 `api.JwtAuthenticator` 会构造它，请求体不能影响这些字段。
- `Identity.scope()`（models.py:42）：把身份投影成 `MemoryScope`，丢弃 purposes/memory_write；api 用它补 scope、postgres 用它做 RLS 会话变量与应用层过滤条件。
- `MemoryScope`（models.py:50）：三字段 scope 值对象；注意它没有任何长度/非空约束（与 Identity 的约束不一致，见批注）。
- `InteractionEvent`（models.py:56）：L0 领域事件：scope + `thread_id` + `idempotency_key` + `source` + `content`(1..100000) + `occurred_at` + `metadata` + `allow_memory_extraction`(默认 False)。由 `EventRequest.to_domain` 合成，HTTP 客户端无法直接指定其中的可信字段。
- `RecallRequest`（models.py:69）：一次召回请求：scope + `thread_id` + `query`(≤10000) + `purpose`（`conversation|task|history|support`）+ `token_budget`(0..4096) + `now`。`now` 是显式传入的时间点，双时态谓词用它而不是数据库 `now()`，便于测试与可重放。
- `MemoryContextItem`（models.py:80）：上下文打包的最小单位：`memory_id/statement/layer/valid_from/valid_to/confidence/source_event_ids`。由 `postgres._context_item` 从 SQL 行构造，是 `retrieval.py` 两个函数的唯一输入类型。
- `MemoryContext`（models.py:90）：`build_context` 的输出：`items` + `rendered_text` + `truncated` + `diagnostics_id`；api 把 `diagnostics_id` 回填到 `X-Memory-Diagnostics-Id` 响应头（api.py:185）。
- `RecordReceipt`（models.py:97）：`record_event` 回执：`event_id/recorded/duplicate/extraction_status`；api 依 `duplicate` 决定返回 200 还是 201（api.py:163）。
- `EventProcessingStatus`（models.py:104）：`GET /v1/memory/events/{id}` 的响应模型：`extraction_status/attempts/error_code/updated_at`；status 来自 outbox，无 outbox 时被 COALESCE 成 `disabled_or_not_applicable`（postgres.py:161）。
- `CommandReceipt`（models.py:112）：命令回执：`command_id`、`status`（`completed|needs_confirmation|deletion_pending`）、`affected_memory_ids`、`deletion_job_id`；`deletion_pending` 时 api 返回 202。
- `MemoryListQuery`（models.py:119）：列表查询参数：`scope` + `layers`（枚举列表）+ `statuses`（默认 `["active"]`，自由字符串）+ `cursor` + `limit`(1..100)。
- `MemorySummary`（models.py:127）：列表/审阅投影：`status/expires_at/kind/confidence/future_utility/affect/policy_version/source_event_ids`；`postgres.list_memories` 逐字段填充（postgres.py:842）。
- `AffectSignal`（models.py:143）：情绪观测值对象：`valence`(-1..1)、`arousal`(0..1)、`labels`、`confidence`(0..1)、`observed_at`、`derived_from`(text|voice|multimodal)；被启发式抽取写入 L1 候选，序列化后存进 `memory_item.affect`。
- `MemoryCandidate`（models.py:152）：抽取器产出的**提案**：`statement/proposed_layer/kind/subject_key/valid_from/valid_to/expires_at/source_event_ids/confidence/future_utility/durability/affect/sensitivity/poisoning_flags`。它不能直接落库，必须先过 `MemoryPolicy.evaluate_candidate`。
- `RememberCommand`（models.py:169）：`remember` 命令：`action` 判别字段 + `scope` + `statement` + `source_event_id`（证据事件）。
- `CorrectCommand`（models.py:176）：`correct` 命令：`memory_id` + `replacement` + `source_event_id`；system 会对 `replacement` 先跑内容硬门。
- `ForgetCommand`（models.py:184）：`forget` 命令：`memory_ids`(1..100)；同步隔离 + 异步删除 job，回执可能是 `deletion_pending`。
- `DoNotRememberCommand`（models.py:190）：`do_not_remember` 命令：`event_ids`(1..100)；把事件标为禁止进入记忆并阻断后续抽取。
- `MemoryCommand`（models.py:196）：上面四个命令的 discriminated union（`discriminator="action"`），`system.execute_command_idempotent` 与 `postgres.execute_command_idempotent` 的入参类型。
- `EventRequest`（models.py:202）：`POST /v1/memory/events` 的 body：`thread_id/source/content/occurred_at/metadata`；**故意不含** tenant/user/agent 与 `allow_memory_extraction`（见 to_domain）。
- `EventRequest.to_domain()`（models.py:209）：唯一的"HTTP 请求 → 领域事件"映射点：scope 取自 `identity`，幂等键取自 `Idempotency-Key` 头，`allow_memory_extraction=identity.memory_write`。因此"是否允许自动抽取"只能由 JWT 授权，body 无法伪造。
- `RecallRequestBody`（models.py:222）：`context:build` 的 body：`thread_id/query/purpose/token_budget/request_time`；无 scope 字段，scope 由 api 从 Identity 补进 `RecallRequest`。
- `RememberRequest`（models.py:230）：remember 的 HTTP body，只有 `statement/source_event_id`，无 scope。
- `CorrectRequest`（models.py:236）：correct 的 HTTP body：`memory_id/replacement/source_event_id`，无 scope。
- `ForgetRequest`（models.py:243）：forget 的 HTTP body：`memory_ids`(1..100)，无 scope。
- `DoNotRememberRequest`（models.py:248）：do_not_remember 的 HTTP body：`event_ids`(1..100)，无 scope。
- `CommandRequest`（models.py:253）：四个请求的 discriminated union；api 用 `isinstance` 逐分支转领域命令（api.py:204），并按 `model_dump_json(exclude={"scope"})` 算 payload hash 做幂等冲突检测。
- `DeletionStatus`（models.py:259）：`GET /v1/memory/deletions/{job_id}` 的响应：`job_id/status/attempts/error_code/created_at/completed_at`。
- `EventJob`（models.py:268）：worker 领取的 outbox 任务：`job_id`(int，outbox 主键) + `event_id` + 完整 `InteractionEvent` + `attempts`；`claim_next_extraction` 返回它，`Consolidator.process` 消费它。
**批注**：
- 定义顺序依赖：`MemorySummary`(127) 先引用 `AffectSignal`(143)，靠第 1 行的 future annotations + Pydantic v2 延迟重建才能成立；删掉第 1 行会直接报未定义注解。具体报错时机（导入期 vs 首次校验）**待确认**（本项目 pydantic==2.14.1）。
- 存在"声明了但没被使用"的字段：`MemoryCandidate.subject_key` 只在 llm 提示词、consolidation 被赋 `None`、测试里出现，`store_candidates` 从不写它；`MemoryCandidate.source_event_ids` 也被 `store_candidates` 忽略（证据固定取 outbox 的 `event_id`，postgres.py:313）。
- 约束不一致：`Identity` 的 id 有 1..128 限制，`MemoryScope` 完全没有；`MemoryListQuery.statuses` 是自由字符串而 `layers` 是枚举；`EventJob.job_id` 是 int，其余 id 全是 uuid 字符串；`MemoryContextItem.confidence` 没有 0..1 边界，而 `AffectSignal`/`MemoryCandidate` 有。
- `CommandReceipt.status` 的 `needs_confirmation` 全仓库没有生产者（postgres 只产出 `completed`/`deletion_pending`），属于为设计文档 §5 预留的状态。
- 32 KiB 上限是个内联魔法数（models.py:12），没有命名常量；且它是字节数不是字符数，中文内容实际容量约为 1/3。

---

## 文件：persistent-memory/src/memory_system/policy.py（65 行）
**文件职责**：**硬门**——所有显式写入与自动候选在落库前必须通过的、无分数可抵消的内容/来源/敏感性/分层准入判定。
**依赖/被依赖**：只 import `re`、`dataclasses`，以及 `models.EventSource/InteractionEvent/MemoryCandidate/MemoryLayer`；被 `system.py`（显式命令预检）、`consolidation.py`（候选过滤）、`bootstrap.py`、`worker.py` 与测试导入。
**逐段讲解**：
- `_SECRET_PATTERNS`（policy.py:6）：5 条正则——PEM 私钥头、`api_key/access_token/client_secret/password` 赋值写法、`sk-/rk-` 长密钥、身份证/信用卡/银行卡号、医疗诊断句式（`诊断为|患有|确诊` + ≤40 字）；命中任一即拒存。
- `_INSTRUCTION_PATTERNS`（policy.py:16）：2 条注入式正则——"忽略之前/以上/系统…规则/指令/提示"、"system prompt / 系统提示词 / 越过权限 / 绕过安全"。
- `WriteDecision`（policy.py:22）：`@dataclass(frozen=True, slots=True)` 判定结果：`allowed` + 可选 `reason`；`reason` 是机器可读 code，api 直接把它塞进 422 响应体 `{"code": reason}`（api.py:149）。
- `MemoryPolicy`（policy.py:28）：无状态策略对象，无 I/O、无构造参数；bootstrap 与 worker 各自 `MemoryPolicy()` 实例化，因此策略天然是"同一版本"的纯函数。
- `MemoryPolicy.version`（policy.py:29）：策略版本串 `"2026-10-first-release-v1"`；`system.execute_command_idempotent` 与 worker 把它写入 `memory_item.policy_version` 和命令审计，是"哪些规则产出了这条记忆"的回放键。
- `evaluate_explicit_memory()`（policy.py:31）：显式内容硬门，先查凭证再查指令式注入，返回 `WriteDecision`；`system._require_allowed` 用它把 remember/correct 拦在存储之前（测试断言此时 repository 调用数为 0）。
- `evaluate_candidate()`（policy.py:38）：自动候选硬门，按固定顺序返回**第一个**拒绝原因：来源不是 `USER` → `poisoning_flags` 非空 → `sensitivity ∈ {high, secret}` → 复用 `evaluate_explicit_memory` 的内容判定 → 分层规则：L1 必须 `expires_at`、L2 必须 `expires_at`、L3 必须 `durability=="stable"` 且 `confidence>=0.85`、L3 还要求事件里出现"记住"、L4 一律拒绝。
**批注**：
- 判定顺序即优先级，且全部是"不可被相似度/重要性抵消"的前置条件，与设计文档 §7.2"硬门先于分数"一致；L4 无条件 Deny 等于关闭了自动写程序记忆（与 close-reading 的结论吻合）。
- L3 的"显式同意"用 `"记住" not in event.content` 做子串匹配，而 `consolidation._REMEMBER_PREFIX` 允许 `请/麻烦/你记住：` 前缀——两处口径不同，可能出现"抽取器认为是显式记住、policy 认为不是"的分歧，建议统一为同一判定函数（**待确认**是否有意）。
- `sensitivity` 在 `MemoryCandidate` 里是自由字符串，硬门却依赖 `{high, secret}` 两个具体值；实际取值完全由抽取器/提示词决定（启发式永远写 `"low"`）。这是"策略依赖未受 schema 约束的抽取器约定"的薄弱点。
- 被拒的自动候选在 `Consolidator.process` 里只是被静默过滤，不写审计、不打日志、不计数；只有显式命令拒绝会经 `MemoryPolicyRejected` 暴露成 422。设计文档要求"policy 决策可观测"，这里是缺口（**待确认**是否有意交给 shadow 模式审核）。
- 正则只作用于 `statement` 文本，不检查 `metadata`；且拒绝码同时是 API 对外 code，等于把内部策略细节透给客户端——便于客户端处理，也便于攻击者试探规则边界。

---

## 文件：persistent-memory/src/memory_system/retrieval.py（43 行）
**文件职责**：召回结果的两个纯函数——按 token 预算贪心打包、把记忆渲染成 data-only 容器文本。
**依赖/被依赖**：只 import `json` 与 `models.MemoryContextItem`（无数据库、无策略依赖）；被 `system.build_context` 导入使用（system.py:19）。
**逐段讲解**：
- `pack_context()`（retrieval.py:6）：入参 `candidates`（已按 `postgres.search` 的 rank 排好序）+ `token_budget`，出参 `(selected, truncated)`；对每项估算 `max(1, (len(statement)+3)//4)` token，一旦"已用 + 本项"超过预算就**立即停止**并返回 `(已选, True)`，全部装下返回 `(全部, False)`。
- `render_data_only()`（retrieval.py:20）：把 items 映射成 `{memory_id, layer.value, statement, confidence, source_event_ids}` 的紧凑 JSON（`ensure_ascii=False`、无空格分隔符），再把 `& < >` 替换成 `\u0026/\u003c/\u003e`，最后包进 `<memory-data trust="untrusted" instruction="never">\n…\n</memory-data>`；空列表返回空串。
**批注**：
- token 估算 `len/4` 是按英文"约 4 字符 ≈ 1 token"校准的；中文 statement 实测约 1 字 1 token，因此中文场景会**低估**约 3–4 倍，预算更容易被突破。属"已知近似"还是缺陷**待确认**（测试用 8 字语句估成 2 token，见 test_memory_system.py:30）。
- "首超即停"意味着顺序完全由 SQL 的 `rank DESC, updated_at DESC` 决定，本模块不重排、不跳过、不降级选小项；也因此 `token_budget=0` 时得到 `([], True)`。
- 渲染只输出 5 个字段，丢掉了 item 上的 `valid_from/valid_to`——时态信息不进入上下文，与设计文档"可以压缩，但不得压缩时间、否定词或限定条件"的倾向相左（**待确认**是否为 v1 有意简化）。
- 转义是在整个 JSON 串上做 `str.replace`，但因为这三个字符只会出现在字符串值内部，替换后仍是合法 JSON；`/` 无需转义，`<` 已转义所以 `</memory-data>` 无法闭合外层标签（被 test_memory_content_cannot_escape_the_data_container 锁定）。
- 标签名与设计文档的 `<retrieved_memories authority="data-only">` 不同（README 采用 `<memory-data>`）；无论叫法如何，标签只是给模型的提示，不是权限控制——真正隔离靠 SQL 过滤与 RLS。

---

## 文件：persistent-memory/src/memory_system/settings.py（51 行）
**文件职责**：唯一配置来源（`MEMORY_*` 环境变量 / `.env`），并把安全不变量做成"启动即失败"的校验。
**依赖/被依赖**：import `typing.Literal`、`pydantic.Field/SecretStr/model_validator`、`pydantic_settings.BaseSettings/SettingsConfigDict`；被 `bootstrap.py`、`worker.py`、`dev.py` 与 `tests/test_dev_auth.py` 导入，每个入口各自 `Settings()` 实例化（不是单例）。
**逐段讲解**：
- `Settings`（settings.py:7）：`BaseSettings` 子类，服务所有可调参数的声明处；没有方法，只有字段 + 一个校验器。
- `model_config`（settings.py:8）：`env_prefix="MEMORY_"`、`env_file=".env"`、`extra="forbid"`——即所有配置来自 `MEMORY_*` 前缀变量或 `.env`，未声明的 `MEMORY_*` 变量或构造参数直接报错；测试用 `_env_file=None` 关掉 `.env` 以保证可重复。
- 连接串字段（settings.py:14-15）：`database_url: SecretStr` 必填；`worker_database_url` 可选（worker 可换角色/连接，`run_worker` 用 `or` 回退到 `database_url`，worker.py:95）。
- 身份/JWT 字段（settings.py:16-20）：`environment`（`development|production`）、`jwt_issuer`、`jwt_audience`、`jwks_url` 可选、`dev_jwt_secret: SecretStr` 可选；bootstrap 把它们原样传给 `JwtAuthenticator`。
- 抽取/模型字段（settings.py:21-26）：`extraction_mode`（`heuristic|shadow|model|hybrid`，默认 `heuristic`）、`model_base_url`、`model_api_key: SecretStr`、`extraction_model`、`embedding_model`、`rerank_model`。
- 数值型策略字段（settings.py:27-31）：`raw_event_retention_days`(≥1，默认 30，maintenance 清理用)、`working_memory_ttl_hours`(≥1，默认 72)、`max_context_tokens`(256..4096，默认 2048，`build_context` 的硬上限)、`worker_concurrency`(1..64，默认 4)、`worker_poll_seconds`(0.1..60，默认 1.0)。
- `validate_security_mode()`（settings.py:33）：`@model_validator(mode="after")`，返回 `self`，启动时校验：production 禁止 `dev_jwt_secret` 且必须 `jwks_url`；development 至少要有 dev secret 或 JWKS；dev secret 长度 ≥32；`shadow/model/hybrid` 必须同时给 `model_base_url` 与 `extraction_model`；任一不满足抛 `ValueError`（进程起不来，而不是运行期降级）。
**批注**：
- 把安全不变量集中在 Settings 而非散在 api/bootstrap，是好的收敛；但 `api.JwtAuthenticator.__init__` 又重复实现了"production 禁 dev secret"和"必须有 JWKS 或 dev secret"两条（api.py:61-64），属于双保险也是两处需同步维护的规则。
- 默认值之间不一致：`max_context_tokens` 默认 2048，而 `RecallRequest.token_budget` 允许到 4096；`build_context` 取 `min`，所以客户端请求 4096 时真实预算仍被压到 2048（除非改环境变量）——默认配置下 4096 上限基本无效。
- `embedding_model`、`rerank_model`、`working_memory_ttl_hours` 是"为未来预留"的配置：声明了但全仓库没有任何读取点（`working_memory_ttl_hours` 只在 settings.py 出现），改这些环境变量不会改变行为；close-reading 也指出 v1 未启用 embedding。
- `extraction_mode` 是唯一真正切换 worker 行为的开关，但 model 模式不校验 `model_api_key`（只查 base_url 与 model 名），无 key 也能启动、到首次抽取才失败——对本地兼容网关合理，对公网模型是延迟失败。
- 生产模式没有强制 TLS 连接串、也没有要求 `worker_database_url` 独立角色，这部分留给部署层（compose/README），配置层不做保证。

---

## 文件：persistent-memory/src/memory_system/runtime.py（7 行）
**文件职责**：Windows 专用的事件循环策略开关，保证 worker 里的 asyncio + psycopg 能跑。
**依赖/被依赖**：只 import `asyncio`、`sys`；唯一调用点是 `worker.main`（worker.py:119）。
**逐段讲解**：
- `configure_event_loop()`（runtime.py:5）：仅当 `sys.platform == "win32"` 时调用 `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`，其余平台 no-op；返回 `None`。目的是绕开 Windows 默认 Proactor 事件循环与 psycopg 异步连接的兼容问题。
**批注**：
- API 进程没用这个函数，而是在 `api.main` 里用 `asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)`（api.py:275）达成同样效果——同一个问题有两套写法，建议统一（worker 用 policy、api 用 loop_factory 是历史分叉）。
- 7 行文件承载的是隐式契约："Windows 上必须有人先调用它"。如果把 worker 当库调用（而不是走 `memory-worker` 入口），就会落到默认 Proactor 循环上。
- `asyncio.set_event_loop_policy` 从 Python 3.14 起已弃用；本项目 `requires-python >=3.12,<3.13`（pyproject.toml:10），当前无问题，但升级 Python 时需要改成 `asyncio.run(..., loop_factory=...)`。

---

## 文件：persistent-memory/src/memory_system/__init__.py（21 行）
**文件职责**：包级 façade，从 `models` 重导出 8 个常用类型并声明 `__all__`。
**依赖/被依赖**：只 import `memory_system.models`；全仓库**没有任何模块 import 这个包根**（`from memory_system import ...` 零命中），所以它目前只是对外/对读者的便利入口。
**逐段讲解**：
- import 块（__init__.py:1）：从 `models` 取 `CommandReceipt、InteractionEvent、MemoryCommand、MemoryContext、MemoryListQuery、MemorySummary、RecallRequest、RecordReceipt` 共 8 个类型。
- `__all__`（__init__.py:12）：显式列出同样 8 个名字，锁死星号导入的公共面。
**批注**：
- 这 8 个名字不构成完整领域接口：`Identity`、`MemoryScope`、`MemoryCandidate`、`EventSource`、`MemoryLayer`、`EventJob`、`DeletionStatus`、`EventProcessingStatus` 以及所有 `*Request` 类都没导出；内部代码一律 `from memory_system.models import ...` 直接取，说明 façade 尚未成为稳定契约（**待确认**是否计划给外部集成方使用）。
- 因为包名与模块同名（`memory_system.models`），`from memory_system.models import X` 与 `from memory_system import X` 都能拿到同一个类，容易让读者误以为两种写法等价、或误以为这里的导出就是全部领域类型。
- 该文件没有版本号/`__version__`，而 `pyproject.toml` 里是 0.1.0；诊断信息里也无法从包元数据读版本。
