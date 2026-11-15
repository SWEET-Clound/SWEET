# Persistent Memory

可直接运行的第一版独立模型记忆服务。它把原始交互保存为证据，通过后台 worker
形成受策略约束的 L1/L2/L3 记忆，并向任意上层 AI 产品提供稳定 HTTP API。

## 五分钟启动

要求：Docker Engine 或 Docker Desktop，支持 `docker compose`。

```bash
docker compose up --build -d
docker compose ps
docker compose exec api memory-smoke --base-url http://localhost:8080
```

独立安装 wheel 时使用 `memory-migrate` 升级数据库；迁移文件随 wheel 打包，不依赖源码仓库
当前工作目录。Compose 的 `migrate` 服务也调用同一入口，迁移失败会阻止 API 和 worker 启动。

成功时最后一条命令输出：

```text
smoke ok: event -> automatic memory -> recall -> deletion
```

服务地址：

- API：<http://localhost:8080>
- OpenAPI：<http://localhost:8080/docs>
- PostgreSQL：`localhost:5432`

Compose 会依次启动 PostgreSQL、执行 Alembic migration、启动 API 和 worker。
本地数据库使用三个隔离角色：migration owner、受 RLS 限制的 API 角色、仅供后台任务的
`BYPASSRLS` worker 角色。

如果曾用旧版 Compose 创建过同名数据卷，需要先执行 `docker compose down -v`。该命令会
删除本项目的本地开发数据库，不能用于保留数据的环境。

## 手工调用

开发 token 只在 `MEMORY_ENVIRONMENT=development` 且配置了至少 32 字符的
`MEMORY_DEV_JWT_SECRET` 时可签发：

```bash
docker compose exec api memory-dev-token
```

生产模式禁止开发密钥并强制 JWKS。token 中的 `tenant_id`、`user_id`、`agent_id`、
`memory_purposes` 和 `memory_write` 是可信授权来源；请求正文不能扩大 scope。

核心 API：

- `POST /v1/memory/events`：幂等记录用户、助手或工具事件；
- `POST /v1/memory/context:build`：构建 token 预算内的 data-only 记忆上下文；
- `GET /v1/memory/profile`：按当前 scope 确定性生成最多 512 tokens 的核心画像投影；只包含
  当前有效、低敏感、稳定、高效用、无投毒标记且无未决冲突的 L3，要求 token 允许请求中的
  `purpose`；该投影不单独落库，可随时从记忆与证据重建；
- `GET /v1/memory/events/{event_id}`：查看自动抽取的 pending/retry/completed/failed；
- `POST /v1/memory/commands`：显式记住、纠正、忘记、禁止记忆；人工确认的程序规则使用
  `remember_procedure` / `correct_procedure`，token 必须包含 `review` purpose，证据必须是当前
  scope 的用户事件；普通 `correct` 不能修改 L4；
- `GET /v1/memory/items`：查看当前 scope 的记忆和证据；有下一页时响应包含
  `X-Next-Cursor`，将其作为下一次请求的 `cursor`；
- `POST /v1/memory/items/{memory_id}:review`：批准或拒绝 quarantined 候选，token 必须
  包含 `review` purpose；
- `GET /v1/memory/deletions/{job_id}`：查看物理删除状态；
- `GET /v1/memory/audit`：读取当前 scope 的审计动作，token 必须包含 `audit` purpose；
- `GET /v1/memory/candidate-decisions`：读取自动候选的允许、隔离和拒绝原因，同样需要
  `audit` purpose；该审计只保存类型、主题、层级、敏感性、投毒标记和策略版本，不复制候选原文；
- `GET /v1/memory/diagnostics/{diagnostics_id}`：解释某次召回的候选数、入选 memory IDs、
  token 预算和截断状态，需要 `audit` purpose；不保存查询原文，也不保存可被字典枚举的普通哈希；
- `GET /health/live`、`GET /health/ready`：进程与数据库健康检查。

所有写请求必须携带 `Idempotency-Key`。同一个 key 和相同 payload 返回原结果；同一个
key 对应不同 payload 返回 `409`。

Python 调用方可使用 `memory_system.client.MemoryClient`。传入自行管理的 `httpx.Client`，
并为其配置 API 地址、Bearer token 和超时。客户端提供事件写入、召回、显式命令和删除状态
查询，返回类型化结果。调用方负责重试，重试写入时复用原幂等键。

`import_events` 逐条回放 `(EventRequest, 幂等键)` 并返回回执。中途失败时，已成功的写入
保留，用原键重跑即可。`iter_memories` 分页导出当前身份可见的记忆摘要，默认只导出活跃记忆。
可传入 `layers` 和 `statuses` 筛选层级和状态，例如导出归档的 L2。
隔离和拒绝状态仍需要 review 用途授权。分页反映各次读取时的数据库状态。
摘要可能包含个人内容，应按业务数据保管。事件导入会走正常抽取策略，摘要导出不包含原始
交互正文，也不用于原样恢复历史版本和证据关系。

首次幂等写入还受 PostgreSQL 原子限流保护：默认每个 `tenant/user/agent` scope 每分钟最多
120 个新事件和 30 个新记忆写入或纠正命令。忘记和禁止记忆命令不占用这项配额。
可用 `MEMORY_EVENT_WRITE_LIMIT_PER_MINUTE`、
`MEMORY_COMMAND_WRITE_LIMIT_PER_MINUTE` 调整。幂等重放不重复计数；超限返回 `429` 和
`Retry-After`。计数桶由所有 API 副本共享并在一天后由 worker 清理。它用于限制已认证 scope
的记忆投毒或失控写入，不替代网关对未认证流量、连接数和请求体字节数的限流。

## 自动记忆行为

默认 `heuristic` 模式无需模型 API，适合直接运行：

- 明确“请记住 X”且通过敏感门控：L3；
- 带明确时间边界的会议、预约、截止或提醒：L2，默认 180 天到期；
- 单次高唤醒负面情绪：L1，按 `MEMORY_WORKING_MEMORY_TTL_HOURS` 到期，默认 72 小时；
- 同一事件可以同时产生 L1 情绪状态和 L2 事件；
- assistant/tool/web 内容不能自动创建用户事实；
- 凭证、秘密、高敏感候选和提示注入信号被拒绝；
- L4 自动写入仍然关闭：模型自评或单次结果不能晋升为程序规则。当前只开放受 `review`
  purpose 保护的人工确认命令，并保留用户事件证据链。

自动抽取只在签名 token 含 `memory_write=true` 时启用。关闭后事件仍可按产品需要留作当前
交互证据，但不会进入自动长期候选。

可选模型模式：

```env
MEMORY_EXTRACTION_MODE=shadow
MEMORY_MODEL_BASE_URL=https://model-gateway.example.com/v1
MEMORY_MODEL_API_KEY=...
MEMORY_EXTRACTION_MODEL=memory-extractor
MEMORY_MODEL_TIMEOUT_SECONDS=30
MEMORY_MODEL_MAX_ATTEMPTS=2
```

模型必须返回结构化候选；输出经过 Pydantic、来源、敏感性、层级、TTL 和投毒策略复核，
模型没有数据库写权限。`shadow` 把通过门控的模型候选写为 `quarantined`，不参与召回；可用
`GET /v1/memory/items?statuses=quarantined` 查看候选，再通过 review 端点批准或拒绝。读取
`quarantined` 或 `rejected` 列表也需要 `review` purpose；两种审核
决定都写入审计。审核达到业务阈值后，`hybrid` 合并确定性规则和模型结果，`model` 仅使用
模型结果。

单次 outbox 处理默认最多调用模型两次（允许配置 1–3 次）：连接异常、429、5xx 或暂时无效的
结构化响应会做短退避重试，其他 4xx 立即失败；随后仍由 outbox 的最多五次长周期退避负责恢复。
最坏模型请求次数因此有明确上限，不会无限重试。

## 数据生命周期

事件写入、记忆命令和审核决定均要求身份令牌包含布尔值 `memory_write=true`。
缺失或为 false 时返回 `403 memory_write_not_allowed`，读接口仍按用途与 scope 校验。
审核决定还需要 `review` purpose。部署升级前，应核对写入客户端令牌包含该授权。

用户确认跨事件摘要后，可通过现有 `remember` 命令保存摘要，将确认事件放在
`source_event_id`，并在 `supporting_event_ids` 提供最多八条原始用户事件 ID。
确认事件与支持事件使用不同的 ID。
服务在同一事务中校验所有事件属于当前 scope、尚未脱敏且允许记忆，再写入摘要与去重后的
证据链接。任一来源不合格时整笔命令回滚。应用迁移 `20261110_0019` 后，确认事件与支持事件
均记录为摘要的必要来源，禁止其中任一条会使整条摘要退出召回并进入物理删除。
升级前已写入的多来源摘要需要重新确认，才能补建必要来源关系。
配置 `MEMORY_CONSOLIDATION_MODEL` 与 `MEMORY_MODEL_BASE_URL` 后，可调用
`POST /v1/memory/consolidations:preview`，请求 `{"event_ids":["事件UUID1","事件UUID2"]}`。
持有 `review` 权限的调用方可先用 `GET /v1/memory/consolidations/sources?thread_id=会话ID`
查找同一会话内最近八条可供筛选的用户事件 ID。接口只返回 ID，预览阶段仍会校验内容和来源。
若调用方没有会话 ID，可先用 `GET /v1/memory/consolidations/threads`，或调用 SDK 的
`discover_consolidation_threads()`，列出最近有至少两条可供筛选事件的 20 个会话。
预览请求也可以直接传 `{"thread_id":"会话ID"}`，由服务端选择该会话最近的合格来源。
Python SDK 对应调用为 `preview_consolidation(thread_id="会话ID")`。
可选来源不足两条时返回空列表。同一请求只能传 `thread_id` 或 `event_ids`。
需要 `review` purpose，可选 2–8 条不同用户事件，正文总计最多 32000 字符。
启用前确认配置的模型网关获准接收这些正文。服务解密并校验 scope、来源、禁止记忆与敏感规则，
最多返回三条通过策略的 L2 摘要建议。模型用请求内的临时编号引用证据，每条建议至少引用两条
不同事件。服务校验引用后转换为真实事件 ID，模型请求中不包含这些 ID。
预览结果由用户审核，确认后记录新的用户确认事件，再用 `remember` 保存摘要及来源。
保存 L2 候选时应一并传入 `expires_at`。有到期时间的 `remember` 写入 L2，
到期时间须晚于当前数据库时间且不超过 180 天；省略该字段时维持原有 L3 语义。
L2 摘要按到期规则退出普通召回，纠正时保留原到期时间。
SDK 提供 `preview_consolidation` 与 `confirm_consolidation(proposal, confirmation_event_id,
idempotency_key)`，确认方法自动带上候选到期时间和来源列表。
调用确认方法前应取得用户确认并记录对应用户事件。
迁移 `20261110_0020` 后，摘要预览默认按 tenant/user/agent 每分钟最多放行十次，
可用 `MEMORY_CONSOLIDATION_LIMIT_PER_MINUTE` 调整。配额在 PostgreSQL 中跨 API 副本共享，
来源校验失败不消耗配额，已放行请求的模型调用失败仍消耗配额。
单次预览仍遵循 `MEMORY_MODEL_MAX_ATTEMPTS` 的重试上限。超限返回
`429 model_rate_limited` 和 `Retry-After`。

#### 后台巩固

Worker 配置 `MEMORY_CONSOLIDATION_MODEL` 和 `MEMORY_MODEL_BASE_URL` 后，可为已授权的
租户、用户和 Agent 生成审核草稿。授权默认关闭。具备 `review` 和记忆写权限的调用方可向
`PUT /v1/memory/consolidations/background` 提交 `{"enabled":true}`。
只具备 `review` 的调用方也可提交 `{"enabled":false}` 撤回授权，未确认草稿随即清除。

Worker 每轮最多成功保存一批草稿，每批使用同一会话中的两到八条合格事件，最多生成
两条 L2 草稿和一条 L4 草稿。后台处理从当前授权之后写入且允许自动抽取的用户事件中选取来源。
每批最多发起两次逻辑模型调用，单次调用仍遵循模型重试配置。批次失败后冷却十分钟。

审核方用 `GET /v1/memory/consolidations/background` 查询 `batches`，用响应中的
`next_cursor` 获取下一页。使用批次的 `source_digest` 调用以下接口。

- `POST /v1/memory/consolidations/background/{source_digest}/confirm` 需要
  `review`、记忆写权限和 `Idempotency-Key`。传入 `ordinal`（从 0 开始）、
  返回的完整 `draft` 和新记录的用户确认事件 `source_event_id`。确认一条草稿后关闭整批。
- `DELETE /v1/memory/consolidations/background/{source_digest}` 需要 `review`，
  用于驳回整批。驳回保留同一来源组合的去重记录。

Python SDK 提供 `set_background_consolidation`、`list_background_consolidation_drafts`、
`confirm_background_consolidation` 和 `dismiss_background_consolidation_drafts`。
撤回任一来源会清除受影响的后台草稿。已经确认的记忆按现有证据依赖和删除流程处理。
后台发现支持按会话分组，也可按同一作用域内有效记忆的 `subject_key` 发现跨会话候选。
只有最近八条同主题的合格来源中至少来自两个会话时，才会生成跨会话候选。
主题匹配只用于选择来源，生成的草稿仍须经过审核。
配置巩固模型后，运行 `memory-eval --configured-consolidation` 可评测内置的四条中文
多事件合成样本。该命令复用生产巩固提示词和引用校验，检查预期摘要、证据和应返回空结果的场景。
评测只向配置的网关发送样本文本，无需启动数据库或写入记忆。
配置向量模型后，还会在每个作用域最近的 32 个带向量事件中查找跨会话事件对。
同一事件有多条可用记忆时，采用最近更新的记忆向量。
比较仅使用当前模型和维度一致的向量，余弦相似度至少为 0.9 时进入同一审核流程。
Worker 指标 `memory_worker_consolidation_discovery_duration_seconds` 按是否启用向量匹配
记录候选发现耗时，计时覆盖数据库查询。

- PostgreSQL 是唯一事实源；
- 人工确认的 L4 只在查询与程序文本匹配时参与召回，不进入常驻核心画像；它始终按不可信
  data-only 上下文渲染，不能授予工具权限或绕过动作确认；
- 每条派生记忆持久化 `subject_key`、敏感级别与投毒标记，供审核和治理查询；
- 每个自动候选都写入隐私收敛的决策账本；即使策略拒绝候选，也可追踪拒绝原因，但被拒绝的
  凭证、隐私或投毒文本不会被复制进审计表；
- 对同一 `subject_key`、同一类型且有效窗口重叠的 L3 陈述，系统以受 RLS 保护的
  `contradicts` 关系保留双方，并用事务 advisory lock 避免并发写入漏检；列表与召回返回
  `conflict_memory_ids`，渲染上下文标记 `requires_confirmation=true`，且未决项降低召回排序；
  这是保守的“待确认”信号，不宣称字符串差异已经证明逻辑矛盾；
- 具备 `review` 和记忆写权限的调用方可通过
  `PUT /v1/memory/items/{memory_id}/conflicts/{other_id}/coexistence` 确认两条记忆可以并存。
  Python SDK 对应方法是 `resolve_memory_conflict_as_coexisting(memory_id, other_id)`。
  两条记忆均保留，重复提交返回 `204`，历史时间点召回仍可看到当时的未决关系；
- 新事件和 extraction outbox 在同一事务提交；
- worker 使用 `FOR UPDATE SKIP LOCKED`、租约恢复和最多五次指数退避；多副本 maintenance
  通过 PostgreSQL advisory lock 保证同一时刻只运行一次；Compose healthcheck 使用 worker
  专用角色执行真实数据库探测；SIGINT/SIGTERM 会停止领取新任务、等待当前 iteration/事务
  完成并关闭连接，Compose 提供 45 秒停机宽限。每个 worker 进程单独轮询物理删除任务，
  即使抽取并发全部在等模型响应，删除仍可继续；
- 删除先把记忆同步标为 `deleting`，随后 worker 删除记忆和证据，并把指定原始事件擦成
  不可召回 tombstone；完成前 canary 会验证记忆、向量、证据链接、候选决策和相关召回诊断
  已移除，且指定事件已被擦除，失败则整笔事务回滚
  并重试；保留 payload hash 是为了阻止同一幂等请求把已删内容复活；
- 到期的 L1/其他临时记忆转为 `expired`；L2 转为 `archived`，不参与普通召回，但仍可在
  `history` 目的下按时间和证据查询，等待复核或后续保留策略处理；
  具有 history 用途授权的调用方也可查询已过期的 L1 及当时的观测，用于追溯；
  过期状态不等于物理删除，彻底移除仍应使用 forget 或 do_not_remember；
- 普通召回中的 future_utility 排序加成随记忆有效起点衰减，未设置起点时使用创建时间。
  L1 有当前查询时点之前的情绪观测时，改用该观测时间，避免新表达继续按旧状态降权。
  L1 半衰期六小时，L2 半衰期三十天，history 查询和 L3/L4 保留原加成。
  衰减只改变排序加成，有效期、查询命中与候选资格仍分别校验；
- 原始事件超过保留期后擦除正文和 metadata，但保留最小证据壳与 ID；
- 完成或失败的 outbox 记录七天后清理；
- 召回诊断与原始事件使用相同保留天数，由 maintenance 定期物理清理；
- 查询命中项优先装入上下文，随后补入最多 512 tokens 的核心画像，再补其他场景候选。
  未入选画像的 L3 仍可按查询召回，
  不会因为画像筛选而从事实源消失；
- 记忆上下文使用 `<memory-data trust="untrusted">` 渲染，不能变成 system instruction。
- 召回上下文仅在 `support` 和 `history` 用途返回结构化情绪观测。记忆正文仍按各用途的召回规则处理。

`remember_procedure` 可传入最多八个 `supporting_event_ids`，用于确认从多次任务中总结出的流程。
使用 `POST /v1/memory/consolidations:preview` 并传入 `kind: "procedure"` 和 2–8 个事件 ID，
可用已配置的巩固模型生成待审核建议。需要 `review` 授权，与事件摘要共用限流。
预览不会写入记忆。确认建议时，另传用户确认事件及建议所依据的事件 ID。
SDK 可调用 `preview_consolidation(event_ids, kind="procedure")`，
再用 `confirm_consolidation(proposal, confirmation_event_id, idempotency_key)` 提交用户确认。
确认事件和支持事件均须来自当前身份范围内的用户。纠正后的 L4 版本继承必需来源，
撤回其中一个来源会让依赖它的版本退出召回并排入删除。
若并发确认在纠正期间追加了必需来源，纠正返回 `409 memory_sources_changed`，
调用方可用原幂等键和原请求重试，服务会重新校验完整来源。

迁移 `20261110_0022` 后，每条新抽取的情绪观测按记忆 ID 和来源事件 ID 单独保存，
包含观测时间、到期时间和入库时间。可通过
`GET /v1/memory/items/{memory_id}/affect-history?purpose=history&limit=50` 查询，
需要令牌具有 history 或请求指定的 support 用途；单次最多返回最新 100 条。
可选 `as_of` 接受带时区的时间，只返回截至该时点已发生且已入库的观测与来源事件。
SDK 提供 `affect_history`。撤回事件后，该事件的观测立即不可见，worker 会物理移除记录；
迁移 `20261113_0026` 会清理历史 L2/L3/L4 的结构化情绪字段及观测记录。
降级只能移除新增约束，已清理的观测无法通过降级恢复。
撤回最新观测时，当前情绪摘要回退到仍获准保留的最新观测。删除记忆会级联删除全部观测。
回退同时收敛有效期，被撤回的较新来源不会继续延长状态保留时间。
L1 召回会按查询时点选择当时已经入库且来源获准保留的最新情绪观测，
并返回该观测的有效期。升级前被覆盖的旧观测无法自动恢复。

## 本地源码运行

需要 Python 3.12。最省事的做法是只用 Compose 启动已配置三角色的数据库和 migration，
再从源码启动 API 与 worker：

```powershell
docker compose up -d db migrate
python -m venv .venv
.venv/Scripts/pip install -e ".[test]"
copy .env.example .env
.venv/Scripts/memory-api
```

另开终端运行 `.venv/Scripts/memory-worker`。`.env.example` 仅含本地开发凭证：API 使用
受 RLS 限制的 `memory_app`，worker 使用 `BYPASSRLS` 的 `memory_worker`。不要让 API 使用
migration owner，也不要把这些默认密码带到共享或生产环境。worker 启动时强制要求
`MEMORY_WORKER_DATABASE_URL`，避免误用 API 角色后在 RLS 下静默空转。

完全自管 PostgreSQL 时，先以管理员创建 `memory` 数据库，执行
`deploy/postgres/init.sql` 创建本地角色，再以 owner URL 执行 Alembic；随后把 `.env` 恢复为
API/worker 的受限 URL。Linux/macOS 使用 `.venv/bin/`。

## 测试

```bash
pytest -q
ruff check src migrations tests
memory-eval
```

`memory-eval` 默认运行包内 `evals/chinese_memory_gold.jsonl`：首批离线样本覆盖明确记忆、短期
情绪、限时事件、非用户来源、凭证/卡号/医疗信息和提示注入。它是防回归基线，不等同于
真实业务金标；上线前仍需加入经过人工标注的真实分布样本。

运行 `memory-eval --configured-extractor [金标路径]` 可使用 worker 的抽取配置。
模型模式会把通过输入过滤的样本文本发给配置的模型服务，并按通过写入策略的候选层级评分。
默认使用内置合成样本，自定义文件应先脱敏并确认获准发送。

完整的真实数据库回归需要 owner、API 和 worker 三个连接地址。三个地址须指向同一个
以 `_test` 结尾的可丢弃数据库，测试会自动执行迁移和清理数据。

Windows PowerShell 中设置连接地址后运行测试：

```powershell
$env:MEMORY_TEST_DATABASE_URL = 'postgresql+psycopg://owner:...@127.0.0.1:5432/memory_test'
$env:MEMORY_TEST_APP_DATABASE_URL = 'postgresql+psycopg://memory_app:...@127.0.0.1:5432/memory_test'
$env:MEMORY_TEST_WORKER_DATABASE_URL = 'postgresql+psycopg://memory_worker:...@127.0.0.1:5432/memory_test'
pytest -q -m postgres
```

Linux 或 macOS 使用 `export NAME='值'` 设置同名变量。请将示例中的地址和凭据替换为
测试库配置。缺少角色地址时，对应的分权回归会跳过。

## 生产部署前必须替换

- 将 `MEMORY_ENVIRONMENT` 设为 `production`，删除开发 JWT secret，配置真实 JWKS；
- 使用 KMS/Vault 或编排平台 secret，不把数据库和模型凭证写进镜像；
- 为 owner、API、worker 使用独立密码和最小权限；
- 在网关或反向代理设置请求体上限；应用层字段上限不能避免服务器先缓冲超大 HTTP body；
- 按真实用户行为校准应用写入阈值，并在网关设置 IP/客户端级连接与请求速率限制；数据库
  scope 限流不能阻止认证前 DDoS，也不能替代异常告警；
- API 与 worker 的生产数据库 URL 必须指定 `sslmode=verify-full`。数据库主机名应与证书
  匹配，并按部署环境配置可信 CA。运行迁移时同样设置 `MEMORY_ENVIRONMENT=production`，
  迁移入口会执行相同校验。另需配置备份、PITR、
  连接池、集中日志和告警；
- 用真实业务金标评估误存、漏存、跨用户隔离、删除完成率和召回质量；
- 在启用模型抽取前运行 shadow mode，并由产品、安全和法务确认敏感类别与保留期。

### 逻辑备份与恢复演练

用 PostgreSQL 客户端连接配置提供地址、TLS 和凭据。备份文件包含业务数据，应存放在受控位置。

```bash
pg_dump --dbname=service=memory_backup --format=custom --file=memory.dump
pg_restore --dbname=service=memory_restore --exit-on-error --single-transaction memory.dump
```

恢复目标使用单独的空数据库。先创建备份中引用的数据库角色，再恢复并检查
`alembic_version`、业务样本、RLS 和角色权限。演练通过后，使用应用与 worker 角色跑一次
写入、召回和删除闭环。逻辑备份只能恢复到快照时点，PITR 还需要物理基备与连续 WAL 归档。

### PostgreSQL 时间点恢复演练

1. 在隔离的 PostgreSQL 16 实例开启 `archive_mode`，配置 `archive_command` 或
   `archive_library`，确认归档端能收到 WAL。使用 `pg_basebackup -X stream` 创建物理基备。
2. 基备完成后写入一条合成探针，执行 `SELECT pg_create_restore_point('memory_probe');`，
   再删除探针并执行 `SELECT pg_switch_wal();`。等待归档中出现包含恢复点的 WAL 段。
   可用 `pg_waldump` 核对恢复点所在的段。
3. 在单独的数据目录和端口启动恢复实例。配置 `restore_command` 从归档把 `%f` 复制到
   `%p`，找不到文件时返回非零。设置 `recovery_target_name = 'memory_probe'`、
   `recovery_target_action = 'promote'`，并在数据目录创建空文件 `recovery.signal`。
   Windows 上使用 `copy /Y` 时，源归档路径使用带引号的 Windows 原生路径。
4. 确认 `pg_is_in_recovery()` 返回 false，且恢复实例中存在源实例已删除的探针。
   对业务副本继续核验迁移版本、密钥可用性、RLS 与角色权限，并用合成数据跑写入、召回和删除。
   在开放流量前，核对恢复点之后的删除与禁止记忆请求，补做仍应生效的撤回。

本地演练结果见 [验收记录](ACCEPTANCE.md)。归档配置与恢复目标的完整语义见
[PostgreSQL 16 连续归档与时间点恢复](https://www.postgresql.org/docs/16/continuous-archiving.html)。

第一版有意不包含 Redis、Kafka、Celery、Neo4j、独立向量数据库或自动 L4。当前召回使用
PostgreSQL FTS、中文 trigram、精确 subject、最近情景和 L3/L1 场景候选，并以 RRF 融合；
配置 `MEMORY_EMBEDDING_MODEL`、`MEMORY_EMBEDDING_DIMENSIONS` 和
`MEMORY_MODEL_BASE_URL` 后，worker 每个维护周期最多向配置的网关发送 16 条符合条件的
L2/L3 记忆正文以生成向量。默认关闭，启用前确认网关可接收这些数据。
API 使用同一配置生成查询向量，并将语义候选融入召回。查询命中敏感内容规则或网关调用
失败时继续使用词法、主题键和时间候选。当前对同一范围内的合格向量做精确余弦比较，
生产启用前应测量真实语料上的召回质量、延迟和模型费用。

迁移 `20261106_0015` 后，可向 API 和 worker 同时注入同一个
`MEMORY_EVENT_ENCRYPTION_KEY`（标准 Base64 编码的 32 字节随机密钥）。
生产模式要求配置该密钥，API 和 worker 启动时会拒绝缺失或无效的密钥。
也可以由平台把密钥挂载为只读文件，设置 `MEMORY_EVENT_ENCRYPTION_KEY_FILE`
和轮换时的 `MEMORY_EVENT_ENCRYPTION_FALLBACK_KEY_FILE` 指向文件路径。
同一把密钥只能配置直接值或文件路径之一，更新挂载后需重启进程以重新读取。
启用后，新写入的原始事件正文和元数据以 AES-256-GCM 密文保存。删除与保留期清理会清空密文。
由部署平台的 secret 管理器分发密钥，迁移期间先停止旧版写入者。应用迁移
`20261106_0016` 后，启用加密的 worker 每次维护最多转换 32 条历史明文事件，
跳过已脱敏或被其他事务锁定的事件。待回填数量可用
`SELECT count(*) FROM memory_event WHERE ciphertext IS NULL AND redacted_at IS NULL` 核对。
转换前的明文仍可能存在于旧页、WAL、备份及副本中。派生记忆正文和检索索引仍需单独保护。
迁移 `20261106_0017` 后可以轮换密钥。先让所有 API 和 worker 保持旧主密钥，
同时用 `MEMORY_EVENT_ENCRYPTION_FALLBACK_KEY` 加载新密钥。确认两把密钥均已部署后，
互换主密钥和备用密钥。所有写入者切换到新主密钥后，在 worker 开启
`MEMORY_EVENT_ENCRYPTION_ROTATE=true`，每轮维护最多转换 32 条非当前密钥的原始事件。
当所有未脱敏行的 `ciphertext_key_id` 都等于当前密钥的 SHA-256 十六进制前 32 字符时，
可关闭轮换并从在线服务移除旧备用密钥。含旧密文的备份恢复仍需要旧密钥。
迁移 `20261106_0018` 后，解密失败的行保留原密文并延后一小时重试。
`memory_event_rotation_deferred` 日志记录批次中的失败数，其余行继续处理。
撤下旧密钥前，应核对所有未脱敏行的密钥标识，包括仍在等待重试的行。

配置 `MEMORY_RERANK_MODEL` 和 `MEMORY_MODEL_BASE_URL` 可启用可选模型重排。
网关需在基础地址下提供 `POST /rerank`，接收 `model`、`query` 和字符串数组 `documents`，
按相关性从高到低返回 `{"results":[{"index":1},{"index":0}]}`，每个输入索引恰好出现一次。
服务最多发送 16 条低敏、无冲突且已命中查询的 L2/L3 候选正文。精确主题命中保留原位置。
启用前确认网关可以接收查询和记忆正文。网关错误或返回无效索引时沿用原有顺序，
并增加 `memory_rerank_fallbacks_total`。

`memory_recall_outcomes_total{purpose,outcome}` 统计有查询文本的召回请求在打包后的结果。
`matched` 表示至少一条查询命中，`context_only` 表示只选到了包含情景候选的兜底上下文，
`profile_only` 表示只选到核心画像，`empty` 表示未选中任何记忆。持续升高的
`context_only` 比例可用于排查跨主题召回或相关候选被预算挤出的情况。它是诊断信号，
无法单独证明语义无关。指标标签仅含用途和结果，不包含用户标识、查询或记忆正文。

将 `deploy/alerts.yml` 加入现有 Prometheus 的 `rule_files`，并采集每个 API 实例的
`/metrics`。默认告警为五分钟内至少 20 次写入限流且持续两分钟，以及十五分钟内至少
100 次有查询的召回、情景兜底比例超过一半且持续十分钟。阈值是初始运维配置，
需要按实际流量调整。独立服务部署应分配不同的 Prometheus `job`，
防止同名用途的指标混合。告警通知由现有 Alertmanager 路由负责。
在 `deploy` 目录执行 `promtool check rules alerts.yml` 和
`promtool test rules alerts.test.yml` 可检查规则及触发、低流量抑制样例。

`memory_http_request_duration_seconds` 提供接口处理耗时的直方图，失败请求也记录耗时。
path 标签使用路由模板，未匹配路径统一为 unmatched，避免把用户输入或具体 ID 放进标签。
例如可用 `histogram_quantile(0.95, sum by (le, path)
(rate(memory_http_request_duration_seconds_bucket[5m])))` 观察各接口的 P95 处理延迟。
该指标截止到响应头就绪，客户端网络传输耗时需由网关或客户端另外测量。

召回阶段耗时可通过 `memory_recall_stage_duration_seconds` 查看，按画像查询、候选检索、
查询向量生成和重排分组。Worker 由独立进程提供指标。Compose 网络内可采集
`http://worker:9091/metrics`。独立运行时设置 `MEMORY_WORKER_METRICS_PORT` 启用，
默认仅监听回环地址，可用 `MEMORY_WORKER_METRICS_HOST` 调整。该端点无鉴权，
应放在受控的监控网络内。`memory_worker_deletion_duration_seconds` 记录有实际工作或
发生错误的删除尝试耗时，包含数据库领取操作。
