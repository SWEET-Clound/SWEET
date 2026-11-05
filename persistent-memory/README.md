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
`GET /v1/memory/items?statuses=quarantined` 查看候选，再通过 review 端点批准或拒绝；两种
决定都写入审计。审核达到业务阈值后，`hybrid` 合并确定性规则和模型结果，`model` 仅使用
模型结果。

单次 outbox 处理默认最多调用模型两次（允许配置 1–3 次）：连接异常、429、5xx 或暂时无效的
结构化响应会做短退避重试，其他 4xx 立即失败；随后仍由 outbox 的最多五次长周期退避负责恢复。
最坏模型请求次数因此有明确上限，不会无限重试。

## 数据生命周期

- PostgreSQL 是唯一事实源；
- 人工确认的 L4 只在查询与程序文本匹配时参与召回，不进入常驻核心画像；它始终按不可信
  data-only 上下文渲染，不能授予工具权限或绕过动作确认；
- 每条派生记忆持久化 `subject_key`、敏感级别与投毒标记，供审核和治理查询；
- 每个自动候选都写入隐私收敛的决策账本；即使策略拒绝候选，也可追踪拒绝原因，但被拒绝的
  凭证、隐私或投毒文本不会被复制进审计表；
- 同一 `subject_key`、同一类型的不同用户陈述不会互相覆盖：系统以受 RLS 保护的
  `contradicts` 关系保留双方，并用事务 advisory lock 避免并发写入漏检；列表与召回返回
  `conflict_memory_ids`，渲染上下文标记 `requires_confirmation=true`，且未决项降低召回排序；
  这是保守的“待确认”信号，不宣称字符串差异已经证明逻辑矛盾；
- 新事件和 extraction outbox 在同一事务提交；
- worker 使用 `FOR UPDATE SKIP LOCKED`、租约恢复和最多五次指数退避；多副本 maintenance
  通过 PostgreSQL advisory lock 保证同一时刻只运行一次；Compose healthcheck 使用 worker
  专用角色执行真实数据库探测；SIGINT/SIGTERM 会停止领取新任务、等待当前 iteration/事务
  完成并关闭连接，Compose 提供 45 秒停机宽限；
- 删除先把记忆同步标为 `deleting`，随后 worker 删除记忆和证据，并把指定原始事件擦成
  不可召回 tombstone；完成前 canary 会验证记忆已消失且事件已被擦除，失败则整笔事务回滚
  并重试；保留 payload hash 是为了阻止同一幂等请求把已删内容复活；
- 到期的 L1/其他临时记忆转为 `expired`；L2 转为 `archived`，不参与普通召回，但仍可在
  `history` 目的下按时间和证据查询，等待复核或后续保留策略处理；
- 原始事件超过保留期后擦除正文和 metadata，但保留最小证据壳与 ID；
- 完成或失败的 outbox 记录七天后清理；
- 召回诊断与原始事件使用相同保留天数，由 maintenance 定期物理清理；
- 核心画像先于按需召回装入上下文、最多占 512 tokens；未入选画像的 L3 仍可按查询召回，
  不会因为画像筛选而从事实源消失；
- 记忆上下文使用 `<memory-data trust="untrusted">` 渲染，不能变成 system instruction。

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

真实数据库测试要求 owner URL `MEMORY_TEST_DATABASE_URL` 和 RLS 受限角色 URL
`MEMORY_TEST_APP_DATABASE_URL` 都指向名称以 `_test` 结尾的同一个可丢弃数据库；测试会自动
执行 migration：

```bash
set MEMORY_TEST_DATABASE_URL=postgresql+psycopg://.../memory_test
set MEMORY_TEST_APP_DATABASE_URL=postgresql+psycopg://memory_app:.../memory_test
pytest -q -m postgres
```

另设 `MEMORY_TEST_WORKER_DATABASE_URL` 指向同一个测试库的专用 worker 角色，可额外运行
应用角色写入、worker 角色抽取与物理删除的分权回归。

## 生产部署前必须替换

- 将 `MEMORY_ENVIRONMENT` 设为 `production`，删除开发 JWT secret，配置真实 JWKS；
- 使用 KMS/Vault 或编排平台 secret，不把数据库和模型凭证写进镜像；
- 为 owner、API、worker 使用独立密码和最小权限；
- 在网关或反向代理设置请求体上限；应用层字段上限不能避免服务器先缓冲超大 HTTP body；
- 按真实用户行为校准应用写入阈值，并在网关设置 IP/客户端级连接与请求速率限制；数据库
  scope 限流不能阻止认证前 DDoS，也不能替代异常告警；
- 配置 PostgreSQL 备份、PITR、TLS、连接池、集中日志和告警；
- 用真实业务金标评估误存、漏存、跨用户隔离、删除完成率和召回质量；
- 在启用模型抽取前运行 shadow mode，并由产品、安全和法务确认敏感类别与保留期。

第一版有意不包含 Redis、Kafka、Celery、Neo4j、独立向量数据库或自动 L4。当前召回使用
PostgreSQL FTS、中文 trigram、精确 subject、最近情景和 L3/L1 场景候选，并以 RRF 融合；
Compose 已使用 pgvector 官方镜像，但向量列和 embedding 仍需选定模型维度并通过金标后启用。
