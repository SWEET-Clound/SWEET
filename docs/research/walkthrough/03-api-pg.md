# 片段：HTTP 接口与持久化

## 文件：persistent-memory/src/memory_system/api.py（278 行）
**文件职责**：把 `MemorySystem` 深模块暴露为带 JWT 认证、幂等头、诊断头与固定错误映射的 HTTP 接口。
**依赖/被依赖**：依赖 fastapi/uvicorn/PyJWT/PyJWKClient/prometheus_client/sqlalchemy.exc；从 `models` 取全部 HTTP DTO，从 `postgres` 取 `IdempotencyConflictError`、`NotFoundError`，从 `system` 取 `MemoryPolicyRejected`、`MemorySystem`；被 `bootstrap.create_application` 调用 `create_app`/`JwtAuthenticator`，自身提供 CLI 入口 `memory-api`。
**逐段讲解**：
- 模块导入（api.py:1-42）：引入认证、指标、异常类型与全部 DTO；写请求的 scope 只从 token 来，body DTO 不声明 tenant/user/agent。
- `_BEARER`（api.py:44）：`HTTPBearer(auto_error=True)`，声明 `Authorization: Bearer` 安全方案并在依赖阶段解包 token；缺/坏凭证由 FastAPI 直接拒绝，不经过下面的 401 分支（见批注）。
- `_HTTP_REQUESTS`（api.py:45-49）：Prometheus Counter，标签 `method`/`path`/`status`，在 HTTP 中间件里逐请求累加。
- `JwtAuthenticator.__init__`（api.py:53-70）：生产环境携带 dev_secret 直接 ValueError；必须给 `jwks_url` 或 `dev_secret`；JWKS 客户端缓存 key set 300 秒；这些约束与 `settings.py:33-50` 的启动校验互为兜底。
- `JwtAuthenticator.__call__`（api.py:72-75）：FastAPI 依赖入口，只把 `HTTPAuthorizationCredentials` 的 token 交给 `authenticate_token`，便于测试直接调后者。
- `JwtAuthenticator.authenticate_token`（api.py:77-115）：dev_secret 走 HS256；否则用 `PyJWKClient` 取签名公钥（同步调用放进 `asyncio.to_thread`）走 RS256/ES256；强制 `exp`/`iss`/`aud`；再校验 `memory_purposes` 必须是字符串列表、`memory_write` 必须是 bool（字符串 `"false"` 也 401，见 tests/test_dev_auth.py:74-101）；最终从 token 的 `tenant_id`/`user_id`/`agent_id` 组 `Identity`，scope 完全由 token 决定。
- 认证错误路径（api.py:114-115）：`PyJWTError`、缺 claim 的 `KeyError`、类型错误、`ValueError`（含 Pydantic `ValidationError`）统一转 `HTTPException(401)` 且不带 detail，避免泄露"缺哪个 claim/哪个字段非法"。
- `create_app`（api.py:118-123）：依赖注入 `system`/`authenticate`/`lifespan` 后创建 FastAPI；bootstrap 与测试都可以替换真实依赖（tests/test_api.py:24-40 用 stub）。
- `diagnostics_header` 中间件（api.py:125-137）：先执行请求，再按 `request.scope["route"].path`（无路由记 `unmatched`）计指标；响应若没有 `X-Memory-Diagnostics-Id` 就补 uuid4，因此异常处理器返回的响应也带诊断头。
- `idempotency_conflict` 处理器（api.py:139-141）：`IdempotencyConflictError` → 409 `{"code":"idempotency_conflict"}`，即同一幂等 key 复用了不同 payload。
- `hidden_not_found` 处理器（api.py:143-145）：`NotFoundError` → 403 `{"code":"scope_invalid_or_forbidden"}`；"不存在"与"越权"共用响应，隐藏目标是否存在。
- `policy_rejected` 处理器（api.py:147-149）：`MemoryPolicyRejected` → 422，`code` 用 policy 给出的 `reason`（由 system.py:66-75 抛出，代表凭证/敏感/注入等硬门拒绝）。
- `database_unavailable` 处理器（api.py:151-153）：`SQLAlchemyError` → 503 `{"code":"memory_unavailable"}`；显式命令必须失败可见，不能吞成 200。
- `record_event` 路由 POST `/v1/memory/events`（api.py:155-165）：`EventRequest.to_domain(identity, Idempotency-Key)` 把 token 的 `memory_write` 映射为 `allow_memory_extraction`（models.py:209-219）；新建返回 201，幂等回放返回 200，body 为 `RecordReceipt`。
- `build_context` 路由 POST `/v1/memory/context:build`（api.py:167-186）：先判 `body.purpose not in identity.allowed_purposes` → 403 `purpose_not_allowed`；再用 `identity.scope()` + body 的 thread/query/purpose/token_budget/request_time 组 `RecallRequest`；最后把响应头 `X-Memory-Diagnostics-Id` 覆盖为 `context.diagnostics_id`（api.py:185）。
- `event_processing_status` 路由 GET `/v1/memory/events/{event_id}`（api.py:188-193）：直接查仓储的 outbox 状态；越权/不存在由 `NotFoundError` → 403；返回 `EventProcessingStatus`（状态、attempts、error_code、updated_at）。
- `execute_command` 路由 POST `/v1/memory/commands`（api.py:195-223）：按 `body` 具体类分派 remember/correct/forget/do_not_remember，并强制 `scope=identity.scope()`（body 无 scope 字段）；`payload_hash = sha256(command.model_dump_json(exclude={"scope"}))`（api.py:214-216）用于区分"同 key 同 payload 重放"与"同 key 不同 payload（409）"；`deletion_pending` 返回 202，其余 200。
- `list_memories` 路由 GET `/v1/memory/items`（api.py:225-241）：`layers`/`statuses` 可重复 query，`limit` 限制 1..100（越界由 FastAPI 返 422），`statuses` 缺省 `["active"]`；cursor 原样交给仓储；返回 `MemorySummary` 列表（无 next cursor 字段）。
- `deletion_status` 路由 GET `/v1/memory/deletions/{job_id}`（api.py:243-248）：查删除 job；不存在/越权 → 403（经 `NotFoundError`）。
- `live`（api.py:250-252）：不触库的存活探针，恒 200。
- `ready`（api.py:254-257）：`repository.ping()` 执行 `SELECT 1`；数据库异常经 `SQLAlchemyError` 处理器 → 503；compose.yml:52-63 的 api healthcheck 调它。
- `metrics`（api.py:259-261）：`generate_latest()` + `CONTENT_TYPE_LATEST` 暴露 Prometheus 文本。
- 状态码总表（api.py:139-261）：事件新建 201/幂等回放 200；context:build 200、purpose 不允许 403；commands 200、deletion_pending 202；items/deletions/event-status 200；无效 token 401、缺 Bearer 由 HTTPBearer 拒绝（历史为 403）、越权/不存在统一 403、同 key 不同 payload 409、策略拒绝与请求校验 422、数据库不可用 503；接口没有 404（`NotFoundError` 一律映射 403）。
- `main`（api.py:266-277）：`uvicorn.Config("memory_system.bootstrap:create_application", factory=True, host="0.0.0.0", port=8080)`；Windows 用 `asyncio.SelectorEventLoop` 跑 `server.serve()`，规避 psycopg 在 Proactor 循环上的问题（worker 侧同类处理在 runtime.py）。
**批注**：
- 缺 `Authorization` 头时由 `HTTPBearer(auto_error=True)` 在进入路由前拒绝，FastAPI 历史实现返回 403 `Not authenticated`，而无效 token 是 401；同一"认证失败"出现两种状态码，契约若要求 401 需要自定义 bearer/异常处理（fastapi==0.162.0 的具体行为本次未运行验证，**待确认**）。
- 只有 `context:build` 校验 `purpose`；events/commands/items/deletions 都不校验 purpose，commands 也不校验 `memory_write`（它只经 `to_domain` 决定是否自动抽取，models.py:218）。这是"memory_write 管自动抽取、不管显式命令"的设计，还是漏检，**待确认**。
- `CommandReceipt.status` 允许 `needs_confirmation`（models.py:112-116），但路由只把 `deletion_pending` 映射为 202，`needs_confirmation` 会落成 200；仓储目前也不产生该状态，属预留字段。
- `X-Memory-Diagnostics-Id` 有两个来源：中间件随机 uuid（api.py:135-136）与 build_context 的业务 `context.diagnostics_id`（api.py:185）；排障时需知道 context:build 返回的是后者。
- 幂等 key 只对写请求强制（events/commands）；`Idempotency-Key` 头 `min_length=1, max_length=200`，缺失或超长由 FastAPI 返 422，而非 400。

---

## 文件：persistent-memory/src/memory_system/bootstrap.py（37 行）
**文件职责**：唯一 composition root，把 Settings、连接池、MemorySystem、认证器装配成 FastAPI 应用。
**依赖/被依赖**：依赖 `api.create_app`/`JwtAuthenticator`、`policy.MemoryPolicy`、`postgres.PostgresRepository`、`settings.Settings`、`system.MemorySystem`；被 `uvicorn.Config` 以 `memory_system.bootstrap:create_application`（factory=True）加载；tests 不走它，直接 `create_app` + stub。
**逐段讲解**：
- 模块级常量：无，只有 import 与两个函数（bootstrap.py:1-10）。
- `create_application`（bootstrap.py:13-36）：`Settings()` 读 `MEMORY_*` 环境变量（14）→ `PostgresRepository.from_url(settings.database_url)` 建 async engine（15，API 用 memory_app 角色）→ `MemorySystem(repository, MemoryPolicy(), settings.max_context_tokens)`（16，context 预算上限）→ `JwtAuthenticator(issuer, audience, jwks_url, dev_secret, environment)`（17-27，生产/开发两种签名路径）→ `create_app(system, authenticate, lifespan)`（36）。import 阶段无副作用，每个进程只装配一次。
- `lifespan`（bootstrap.py:29-34）：`asynccontextmanager`，启动阶段不迁移、不 ping，直接 yield；关闭时 `finally` 调 `repository.close()` 释放连接池。迁移由 compose 的 `migrate` 容器执行 alembic（compose.yml:26-37），worker 是独立进程。
**批注**：
- 启动不校验数据库可用性，DB 不可用时进程仍起来，由 `/health/ready` 暴露 503；这符合"启动快、就绪探针负责"的取舍，但需要部署侧保证 healthcheck 被用于流量摘除。
- `Settings()` 的 `env_prefix="MEMORY_"`、`extra="forbid"`（settings.py:8-12）使配置键与 compose 环境变量严格对应；`dev_secret` 在 production 下由 api.py:61-62 与 settings.py:35-39 双重禁止。

---

## 文件：persistent-memory/src/memory_system/worker.py（121 行）
**文件职责**：后台进程编排：抽取 outbox 任务、异步删除 job、周期 maintenance，并负责抽取器与 Consolidator 的装配。
**依赖/被依赖**：依赖 `consolidation`（Consolidator/HeuristicExtractor/HybridExtractor）、`llm.OpenAICompatibleExtractor`、`policy.MemoryPolicy`、`postgres.PostgresRepository`、`runtime.configure_event_loop`、`settings.Settings`；调用仓储的 `claim_next_extraction`/`complete_extraction`/`fail_extraction`/`process_one_deletion`/`run_maintenance`；CLI 入口 `memory-worker`。
**逐段讲解**：
- 模块级常量：无。
- `process_one_extraction`（worker.py:13-25）：`claim_next_extraction()` 无活返回 False；有活则 `consolidator.process(repository, job)`（consolidation.py:136-145，内部调 `store_candidates`）；任何异常捕获后 `fail_extraction(job_id, 异常类名)` 并仍返回 True（job 已被消费、worker 保持存活）；成功则 `complete_extraction`。
- `process_one_deletion`（worker.py:28-35）：委托 `repository.process_one_deletion()`；异常只记 `memory_deletion_failed` 日志并返回 False，一个删除 job 失败不阻塞其它后台工作。
- `process_worker_iteration`（worker.py:38-48）：先抽取后删除，`process_one_deletion() or worked` 表示任一有产出即算"有活"；整轮捕获异常并记 `memory_worker_iteration_failed`，防止数据库抖动杀死进程。
- `build_extractor`（worker.py:51-66）：heuristic → `HeuristicExtractor()`；shadow/model → `OpenAICompatibleExtractor(base_url, model, api_key)`；hybrid（其余值）→ `HybridExtractor(heuristic, model)`；model 模式的配置完整性由 settings.py:46-49 在启动时校验。
- `worker_loop`（worker.py:69-77）：`while True` 反复 `process_worker_iteration`，只有无活时才 `asyncio.sleep(poll_seconds)`，有活立即抢下一单。
- `maintenance_loop`（worker.py:80-90）：循环执行 `run_maintenance(raw_event_retention_days)`，异常只记 `memory_maintenance_failed`；每轮固定 sleep 60 秒，无 leader 选举。
- `run_worker`（worker.py:93-115）：`Settings()` → `worker_database_url or database_url`（95，worker 角色优先）→ engine；`Consolidator(extractor, MemoryPolicy(), write_status="quarantined" if extraction_mode=="shadow" else "active")`（97-101）；`asyncio.gather(maintenance_loop, [worker_loop] * worker_concurrency)`（104-110）；`CancelledError` 记 `worker_stopped` 后 re-raise；`finally` 关闭 engine。
- `main`（worker.py:118-120）：`configure_event_loop()`（runtime.py，Windows 事件循环适配）后 `asyncio.run(run_worker())`。
**批注**：
- worker 调用的仓储方法全部不 `_set_scope`，依赖连接角色 BYPASSRLS：init.sql:2 建了 `memory_worker ... BYPASSRLS`，compose.yml:71 用该角色；若只配 `MEMORY_DATABASE_URL` 回退到 memory_app（worker.py:95），RLS 下 outbox 查询会为空、worker 静默空转（**待确认**是否允许该回退）。
- 没有设计文档 §23 提到的 advisory-lock leader 选举：每个 worker 副本都跑 maintenance_loop，语句本身幂等但会产生并发竞争/写放大（**待确认**是否接受）。
- `process_one_extraction` 把失败也返回 True，配合 `available_at` 退避（postgres.py:340-354）不会造成空转；但失败计数只保留异常类名，`error_code` 信息量有限。
- shadow 只把候选写成 `quarantined`，抽取器仍是模型；heuristic 模式不会产生 quarantine，审核闭环（README 的 `statuses=quarantined`）只在模型模式下有意义。

---

## 文件：persistent-memory/src/memory_system/dev.py（122 行）
**文件职责**：开发期工具：签发仅开发环境可用的 JWT，以及跑通"事件→自动记忆→召回→删除"的 smoke 流程。
**依赖/被依赖**：依赖 `settings.Settings`、`httpx`、`jwt`；被 pyproject 入口 `memory-dev-token`/`memory-smoke` 调用；签出的 token 由 api.py 的 `JwtAuthenticator`（dev_secret/HS256 分支）验证。
**逐段讲解**：
- 模块级常量：无。
- `issue_dev_token`（dev.py:12-36）：`environment != "development"` 或没有 dev_secret 时直接 RuntimeError（防止生产误签）；HS256 签发 `iss`/`aud`/`iat`/`exp(now+lifetime)` + tenant/user/agent + 固定四 purpose（conversation/task/history/support）+ `memory_write=True`。
- `token_main`（dev.py:39-55）：argparse `--tenant/--user/--agent/--minutes`（默认 demo-*/60 分钟），`Settings()` 后打印 token；入口 `memory-dev-token`。
- `smoke_main`（dev.py:58-122）：`Settings()` + 签发 smoke 身份 token；生成随机 `event_key`/`command_key`；`httpx.Client(base_url, headers=Bearer, timeout=5)`；步骤① POST `/v1/memory/events`；② 轮询 GET `/v1/memory/items` 直到出现 `statement == "我喜欢简洁回答"`（dev.py:87，依赖 heuristic 抽取结果）；③ POST `/v1/memory/context:build` 并断言 `items` 非空；④ POST `/v1/memory/commands` 发 forget；⑤ 轮询 GET `/v1/memory/deletions/{id}` 直到 `completed`，打印 `smoke ok: event -> automatic memory -> recall -> deletion`；任意阶段超时抛 RuntimeError。
**批注**：
- 期望语句是 HeuristicExtractor 的固定产物（consolidation.py），把 `MEMORY_EXTRACTION_MODE` 换成 model/shadow 后该断言大概率不成立；smoke 还要求 worker 已在线并消费 outbox。
- `--timeout` 只作为轮询 deadline（dev.py:82/115），HTTP 超时写死 5 秒（dev.py:69），两个"超时"语义不同；`deadline` 从事件写入后开始算，删除轮询复用了同一个 deadline。
- 每次 smoke 都用 uuid4 生成幂等 key（dev.py:66-67），所以 smoke 自身不验证幂等回放；幂等由 tests/test_api.py 与 tests/test_postgres_integration.py 覆盖。
- token 的 purpose 集合写死四个，无法用它模拟"只允许 conversation"的受限 token；受限场景只能在测试里手工签发（tests/test_dev_auth.py:24）。

---

## 文件：persistent-memory/src/memory_system/postgres.py（1125 行）
**文件职责**：用 SQLAlchemy async Core + 原生 SQL 封装全部持久化、事务、锁、幂等与 RLS 行为，是 PostgreSQL 这一唯一事实源在应用侧的唯一入口。
**依赖/被依赖**：依赖 sqlalchemy 的 `text`/`AsyncEngine`/`AsyncConnection`/`create_async_engine` 与 `models` 的 DTO/枚举；被 `bootstrap`（构造）、`system`（record_event/build_context/execute_command/list_memories）、`worker`（claim/complete/fail/deletion/maintenance）、`api`（`repository.event_processing_status`/`deletion_status`/`ping`）调用。表结构、索引与 RLS 策略在 migrations/versions/20261029_0001_initial.py。
**逐段讲解**：

**区域 1：模块、连接生命周期与 scope 基础设施**
- `IdempotencyConflictError`（postgres.py:35-36）/`NotFoundError`（39-40）：空实现领域异常；仅用于让 api.py:139-153 映射到 409/403。
- `PostgresRepository.__init__`（43-45）：只保存 `AsyncEngine`；无全局连接。
- `from_url`（47-49，classmethod）：`create_async_engine(database_url, pool_pre_ping=True)`；`pool_pre_ping` 让坏连接在借出时被替换，但不做启动探活。
- `close`（51-52）：`engine.dispose()`，由 bootstrap lifespan 的 finally 与 worker 的 finally 调用。
- `ping`（54-56）：`SELECT 1`；不设 scope、不触业务表，供 `/health/ready` 使用。
- `_set_scope`（1010-1021，@staticmethod）：一条 SQL 里 `set_config('app.tenant_id'/user_id/agent_id', ..., true)`；`is_local=true` 即事务级，必须处在事务中；RLS 策略用 `current_setting(..., true)` 比较（migration:171-210），未设置时返回 NULL，策略为假、零行可见——这是"漏设 scope 就查不到"而非"全放行"的关键。
- `event_scope`（1120-1125）：模块级函数，从 `InteractionEvent` 取三个 scope 字段组 `MemoryScope`，供 `record_event`/`store_candidates` 复用。

**区域 2：事件写入与幂等**
- `record_event`（58-150）：先用 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",",":"))` 把 thread/source/content/occurred_at/metadata 规范化后 sha256 成 `payload_hash`（59-71，metadata 也参与哈希）；`engine.begin()` 事务内先 `_set_scope`（80）；`INSERT INTO memory_event ... ON CONFLICT (tenant_id,user_id,agent_id,idempotency_key) DO NOTHING RETURNING id`（83-93）——唯一约束来自 migration:34，冲突时不报错而是返回空行；插入成功且 `source=user && allow_memory_extraction` 时再插入 `memory_outbox(kind='extract', status='pending')`（101-112），同一事务保证"事件与 outbox 原子"；`source=user` 但不允许抽取记 `disabled`，非 user 记 `not_applicable`（99-115）。冲突分支（123-150）：按 scope+key 查回原行，`payload_hash` 不同 → `IdempotencyConflictError`（134-135）；相同则查该事件最新一条 outbox 状态（`ORDER BY id DESC LIMIT 1`）作为 `extraction_status` 返回 `recorded=False, duplicate=True`。
- `event_processing_status`（152-186）：`_set_scope` 后 `LEFT JOIN LATERAL` 取该事件最新 outbox（167-170），`COALESCE(o.status,'disabled_or_not_applicable')`、`attempts` 缺省 0，`updated_at` 按 completed_at→locked_at→created_at→recorded_at 回退（164-165）；WHERE 同时匹配 event id 与三 scope，查不到抛 `NotFoundError`（178-179）→ 403。

**区域 3：派生记忆的写入、版本与状态**
- `store_candidates`（233-325）：worker 抽取落库入口。事务内 `_set_scope`（244）；先 `SELECT prohibited_from_memory FROM memory_event ... FOR UPDATE`（245-258），事件不存在或已被"不要记住"则直接返回 `[]`——防止与删除操作并发把新记忆写回来；每个候选计算 `sha256("{kind}\0{归一化 statement}")` 指纹（260-262）；`INSERT INTO memory_item ... ON CONFLICT (tenant_id,user_id,agent_id,fingerprint) WHERE fingerprint IS NOT NULL DO UPDATE`（267-292）：冲突时 `confidence=GREATEST(...)`、`expires_at=GREATEST(...)`、`quarantined` 且新值为 `active` 时提升为 `active`、`updated_at=now()`，即"同一事实多证据合并"而不是新增重复行；随后 `INSERT INTO memory_evidence(memory_id,event_id) ON CONFLICT DO NOTHING`（313-322）并 `_audit(...,"automatic_memory", stored_id)`（323）；返回的 id 在冲突时是已有记忆的 id。
- `execute_command_idempotent`（356-462）：所有显式命令的唯一写入口，一个事务内完成"去重行 + 业务副作用 + 回执"。先 `INSERT INTO command_dedup ... status='processing' ON CONFLICT DO NOTHING RETURNING idempotency_key`（366-379）；插入失败说明已有记录，读取 `payload_hash, receipt`（381-394），哈希不同抛 `IdempotencyConflictError`，相同则直接返回已存回执（`CommandReceipt.model_validate(row["receipt"])`）；首次插入则按 `RememberCommand`/`CorrectCommand`/`ForgetCommand`/`DoNotRememberCommand` 分派（397-445），最后 `UPDATE command_dedup SET status='completed', receipt=..., completed_at=now()`（447-461）。因为去重行与副作用同事务提交，命令失败会整体回滚，重试可安全重放；"先返回后写库"在此结构下不可能发生。
- `remember`（464-475，公开包装）：`engine.begin()` + `_set_scope` 后委托 `_remember`，供测试与其他调用方绕过 dedup 时使用。
- `_remember`（477-540）：指纹前缀 `"explicit\0"`（486-488），与候选指纹区分命名空间；先 `SELECT id FROM memory_event ... prohibited_from_memory=false FOR UPDATE`（489-501），引用事件不存在/已被禁止 → `NotFoundError`（防伪造证据）；`INSERT memory_item` 固定层 `L3`、`kind='explicit'`、`status='active'`、`confidence=1.0`、`valid_from=now()`、`future_utility='high'`、`durability='stable'`（502-528）；冲突时仅把 `quarantined` 记忆激活并把置信度重置 1.0（516-525）；再写证据（538）与审计 `remember`（539）。
- `correct`（542-559，公开包装）：同 `remember`，事务内委托 `_correct`。
- `_correct`（561-620）：先 `SELECT ... FROM memory_item WHERE id=:memory_id AND scope AND status='active' FOR UPDATE`（572-582）锁住被更正版本；再校验 source event 属于同 scope 且未被禁止（583-592），任一不满足抛 `NotFoundError(memory_id)`（593-594，不区分"不存在"与"事件非法"）；接着把旧行 `status='superseded', valid_to=now()`（595-598），插入新行 `kind='explicit_correction'`、`confidence=1.0`、`supersedes_id=旧 id`（599-617），实现双时态 + 版本链；最后写新记忆的证据与审计 `correct`（618-619），返回新 id。

**区域 4：证据与关系**
- 证据写入点：`store_candidates`（313-322）、`_remember`（538）、`_correct`（618）都调 `_add_evidence`（997-1008）；SQL 为 `INSERT INTO memory_evidence(memory_id,event_id) ON CONFLICT DO NOTHING`，主键 (memory_id,event_id)（migration:77-82）保证不重复计证据。
- 关系：schema 只有 `memory_evidence` 与 `memory_item.supersedes_id` 自引用（migration:54、77-82），没有设计文档 §8.3 的固定五种关系表；"证据链可追溯"由"事件→evidence→记忆"承担。
- 级联：`memory_evidence.memory_id`/`event_id` 都是 `ON DELETE CASCADE`（migration:78-79），删记忆或删事件时证据自动消失；本文件从不显式删 evidence（`_prohibit_events` 例外，708-717）。
- `_add_evidence`（997-1008，@staticmethod）：三个写入点共用的证据插入；不做 scope 校验，依赖调用方已设 scope 且 memory/event 已按 scope 校验。

**区域 5：outbox 领取与标记**
- `claim_next_extraction`（188-231）：`WITH picked AS (SELECT id FROM memory_outbox WHERE kind='extract' AND ((status IN ('pending','retry') AND available_at<=now()) OR (status='processing' AND locked_at<now()-interval '5 minutes')) ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) UPDATE ... SET status='processing', locked_at=now(), attempts=attempts+1 ... RETURNING id,event_id,attempts`（190-211）。要点：`FOR UPDATE SKIP LOCKED` 支持多 worker 并行抢单不互等；`processing` 超 5 分钟视为死锁回收；`attempts` 在领取时自增，因此 fail 里的 `attempts>=5` 即第五次失败进 `failed`。拿到 job 后再读事件行组 `EventJob`（215-231）；整段不 `_set_scope`（worker 角色）。
- `complete_extraction`（327-338）：`UPDATE memory_outbox SET status='completed', completed_at=now(), locked_at=NULL, error_code=NULL WHERE id=:id AND status='processing'`；`AND status='processing'` 防止把已被"不要记住"取消为 failed 的行改回 completed。
- `fail_extraction`（340-354）：`status=CASE WHEN attempts>=5 THEN 'failed' ELSE 'retry' END`，`available_at=now()+make_interval(secs => LEAST(300, attempts*attempts))`（二次退避，封顶 300 秒），清锁并保存截断到 120 字符的 `error_code`；同样带 `AND status='processing'`。

**区域 6：召回查询**
- `search`（743-805）：只读 `engine.connect()` 但先 `_set_scope`（751-752）。排名表达式（760-769）为 `ts_rank_cd(search_vector, websearch_to_tsquery('simple', :query)) + similarity(statement, :query) + future_utility 加权(high 0.20/medium 0.08) + layer 加权(L3 +0.20, L1 在 purpose='support' 时 +0.10)`；过滤条件（772-791）：三 scope、`status='active'`（`purpose='history'` 时也允许 `superseded`）、非 history 时要求 `valid_from<=now<valid_to`、`expires_at` 未过期、以及"FTS 命中 OR trigram 相似度>0.05 OR layer='L3' OR (L1 且 purpose='support')"的候选门；`LEFT JOIN memory_evidence` + `array_agg(...) FILTER` 聚合证据 id，`GROUP BY m.id`；`ORDER BY rank DESC, updated_at DESC LIMIT :limit`。返回 `MemoryContextItem` 列表（805），不返回原始事件内容。
- `list_memories`（807-858）：动态拼 WHERE（808-824）：三 scope + `status = ANY(CAST(:statuses AS text[]))`，可选 `layer = ANY(...)`、`id < CAST(:cursor AS uuid)`；SQL 由固定片段拼接、值全走参数（无注入面）；`ORDER BY m.id DESC LIMIT :limit`；`LEFT JOIN memory_evidence` 聚合 `event_ids`（829-830）；构造 `MemorySummary`（841-858，`affect` 为 jsonb 直接交给 Pydantic）。无 `next_cursor` 计算，keyset 依赖 id 排序而非时间。
- `_context_item`（1107-1117，@staticmethod）：把 search 行映射为 `MemoryContextItem`（id/statement/layer/valid_from/valid_to/confidence/source_event_ids）。

**区域 7：审计**
- `_audit`（1023-1036，@staticmethod）：`INSERT INTO memory_audit(id, tenant_id, user_id, agent_id, action, target_id)`；只在写路径调用（`automatic_memory`/`remember`/`correct`/`forget`/`do_not_remember`），不记录原文/embedding/prompt；`memory_audit` 同样启用 RLS，且无删除维护逻辑，审计会一直保留。

**区域 8：删除 job（同步隔离 + 异步清理 + 事件脱敏）**
- `isolate_memories`（622-627，公开包装）/`_isolate_memories`（629-658）：`sorted(set(ids))` 归一化（635）；`_lock_deletion_target`（636）拿建议锁；`_find_active_deletion_job(..., "memory_ids", normalized)` 查是否有同集合的在途 job，有则直接复用其 id（637-641，命令级幂等）；否则 `UPDATE memory_item SET status='deleting', deleted_at=now() WHERE ... id=ANY(uuid[]) AND status<>'deleting' RETURNING id`（642-653）——同步让目标不可召回；无命中返回 `([], None)`（654-655）；有命中则 `_create_deletion_job`（656）、审计 `forget`（657），返回受影响的 id 与 job id（api.py 据此给 202/200）。
- `prohibit_events`（660-665）/`_prohibit_events`（667-741）：同样归一化 + 建议锁 + 在途 job 复用（673-679）；`UPDATE memory_event SET prohibited_from_memory=true ... RETURNING id`（681-694）；把该事件 `pending/processing/retry` 的 outbox 行取消为 `failed, error_code='prohibited_from_memory'`（696-707），阻止尚未执行的抽取；同步 `DELETE FROM memory_evidence WHERE event_id=ANY(...) RETURNING memory_id`（708-717）——先解除证据；对因此失去全部证据的记忆 `UPDATE memory_item SET status='deleting', deleted_at=now() WHERE ... NOT EXISTS(evidence)`（720-736）；最后为"失去证据的记忆 + 被禁止的事件"建一个删除 job（737-739），审计 `do_not_remember`（740）。事件正文的物理脱敏延迟到 worker 的 `_apply_deletion`。
- `deletion_status`（860-884）：按 id + 三 scope 查 `deletion_job`，查不到抛 `NotFoundError`→403；返回 status/attempts/error_code/created_at/completed_at。
- `process_one_deletion`（886-915）：第一个事务里用同样的 `FOR UPDATE SKIP LOCKED` + 5 分钟超时回收领取 job（891-906），`ORDER BY created_at`（先来先删）；提交认领后退出事务，再在新事务调 `_apply_deletion`（910-911）；异常时 `_fail_deletion(job_id, 异常类名)` 后 re-raise（912-914），worker.py:28-35 捕获并记日志。
- `_apply_deletion`（917-943）：`DELETE FROM memory_item WHERE id=ANY(uuid[])`（919-922）触发 evidence 的 `ON DELETE CASCADE`；`UPDATE memory_event SET content='[deleted-by-user]', metadata='{}'::jsonb, prohibited_from_memory=true, redacted_at=now()`（923-933）保留事件行与幂等 hash，只抹内容；最后把 `deletion_job` 置 `completed`（934-943），三步同事务——要么全部生效要么可重试。
- `_fail_deletion`（945-959）：与 `fail_extraction` 同构：attempts>=5 → `failed` 并写 `completed_at`，否则 `retry` 并二次退避到 `available_at`，清锁、截断 error_code。
- `_lock_deletion_target`（1062-1077，@staticmethod）：把 scope+target_kind+排序后的 ids 序列化成 JSON，`pg_advisory_xact_lock(hashtextextended(key, 0))`；同一批删除目标串行化，避免并发重复建 job（deletion_job 没有唯一约束兜底）。
- `_find_active_deletion_job`（1038-1060，@staticmethod）：先白名单校验列名只能是 `memory_ids`/`event_ids`（1045-1046，防 SQL 注入）；用 `col @> ids AND col <@ ids` 要求集合完全相等（1053-1054），再按 `created_at DESC LIMIT 1`；`status IN ('pending','processing','retry')` 才算在途。
- `_create_deletion_job`（1079-1105，@staticmethod）：插入新 job，`uuid` 主键、`memory_ids`/`event_ids` 以 `CAST(... AS uuid[])`、状态 `pending`。

**区域 9：维护循环（worker 专用）**
- `run_maintenance`（961-995）：一个事务内做三件事：① `UPDATE memory_item SET status='expired' WHERE status='active' AND expires_at<=now()`（963-971）；② 把超过 `raw_event_retention_days` 的原始事件正文脱敏为 `[retention-expired]`、metadata 清空、写 `redacted_at`（972-986），条件是"该事件没有仍在 pending/processing/retry 的 outbox 行"，避免抽取还没跑就把证据抹掉；③ 清理 7 天前的 `completed`/`failed` outbox 行（987-995，窗口写死）。语句不带 scope，依赖 worker 角色的 BYPASSRLS；不会删除 `memory_audit`/`memory_item`。

**批注**：
- **RLS 是双角色设计，不是单一路径**：所有 API 路径方法（`record_event`/`event_processing_status`/`store_candidates`/`execute_command_idempotent`/`remember`/`correct`/`isolate_memories`/`prohibit_events`/`search`/`list_memories`/`deletion_status`）都先 `_set_scope`；worker 专用方法（`claim_next_extraction`/`complete_extraction`/`fail_extraction`/`process_one_deletion`/`_apply_deletion`/`_fail_deletion`/`run_maintenance`）全部依赖 `memory_worker` 的 BYPASSRLS（init.sql:2、migration:171-210）。只要 worker 误用 app 角色，outbox 会查空（**待确认**回退路径）。
- **授权与 SQL 的一处可能不一致**：migration:216-223 给 `memory_app` 的授权是 `SELECT, INSERT, UPDATE ON memory_evidence`，没有 `DELETE`；而 `_prohibit_events` 在 API 事务内执行 `DELETE FROM memory_evidence`（708-717）。若部署按 compose 用 memory_app 连接（compose.yml:46），do_not_remember 很可能抛 `insufficient_privilege` → 503；现有集成测试的该路径都用 owner 测试库（tests/test_postgres_integration.py:269、315 走 `_test_database_url`），app 角色只被 RLS 隔离测试使用（同文件:151），未覆盖该 DELETE（**待确认**，建议以 memory_app 连接复现）。
- **supersedes 链与物理删除的 FK 风险**：`memory_item.supersedes_id` 是 `REFERENCES memory_item(id)`、无 `ON DELETE` 处理（migration:54），而 `_apply_deletion` 直接 `DELETE memory_item`（919-922）：若被删版本仍被更新版本引用（例如 forget 一条已被 correct 的旧记忆），外键可能阻止删除，job 重试 5 次后 failed（**待确认**，需按 DDL 实测）。
- **错误语义不一致**：`forget`/`do_not_remember` 对不存在或不属于本 scope 的 id 返回 `completed` + 空列表（postgres.py:654-655、693-694），而 `remember`/`correct`/`deletion_status` 抛 `NotFoundError`→403；前者是"不泄露存在性"的静默成功，后者是隐藏式 403，契约上未统一，**待确认**是否有意为之。
- **候选字段落库不全**：`store_candidates` 写入了 statement/layer/kind/status/confidence/valid_*/expires_at/fingerprint/future_utility/durability/affect，但 `MemoryCandidate` 的 `subject_key`/`sensitivity`/`poisoning_flags`/`source_event_ids`（models.py:152-166）在 `memory_item` 里没有对应列、也未被使用；证据固定为"当前事件"，与设计文档 §8.2 的治理/投毒字段有漂移（**待确认**是否后续迁移补齐）。
- **召回是 v1 的加权混合而不是设计文档的 RRF/向量**：`search` 只用 FTS + trigram + utility/layer 加权，没有 embedding/重排；与 README 的 v1 取舍一致，但与设计 §14 的候选融合流程不同。
- **list 的 keyset 语义**：`ORDER BY m.id DESC` 用的是 UUID 排序（808-837），cursor 与时间无关，且接口不返回 next cursor；客户端只能取最后一条 `memory_id` 当 cursor；`statuses` 不校验，传 `quarantined` 即可做审核列表（README 也这样用）。
- **claim/apply 跨两个事务**：`process_one_deletion` 认领提交后再执行删除（886-915），认领后崩溃会由 5 分钟 `locked_at` 回收；`_apply_deletion` 本身原子，因此删除最多重复执行、不会半完成。
- **record_event 的 tombstone 语义**：payload hash 与内容无关地留在 `memory_event`，`do_not_remember` 后重放同 payload 仍返回 duplicate（123-150），不会复活记忆；内容已脱敏为 `[deleted-by-user]`，这正是"erasure tombstone + payload hash 防复活"的实现位置。
- **maintenance 的保留窗口**：raw event 默认 30 天（settings.py:27）、outbox 清理硬编码 7 天（992），`memory_audit` 永久保留；过期记忆只置 `expired` 不删除，可审计但会持续占用存储（**待确认**是否符合保留策略）。
