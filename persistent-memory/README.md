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
- `GET /v1/memory/events/{event_id}`：查看自动抽取的 pending/retry/completed/failed；
- `POST /v1/memory/commands`：显式记住、纠正、忘记、禁止记忆；
- `GET /v1/memory/items`：查看当前 scope 的记忆和证据；
- `GET /v1/memory/deletions/{job_id}`：查看物理删除状态；
- `GET /health/live`、`GET /health/ready`：进程与数据库健康检查。

所有写请求必须携带 `Idempotency-Key`。同一个 key 和相同 payload 返回原结果；同一个
key 对应不同 payload 返回 `409`。

## 自动记忆行为

默认 `heuristic` 模式无需模型 API，适合直接运行：

- 明确“请记住 X”且通过敏感门控：L3；
- 带明确时间边界的会议、预约、截止或提醒：L2，默认 180 天到期；
- 单次高唤醒负面情绪：L1，24 小时到期；
- 同一事件可以同时产生 L1 情绪状态和 L2 事件；
- assistant/tool/web 内容不能自动创建用户事实；
- 凭证、秘密、高敏感候选和提示注入信号被拒绝；
- L4 自动写入在第一版关闭，必须等可信任务结果证据链完成。

自动抽取只在签名 token 含 `memory_write=true` 时启用。关闭后事件仍可按产品需要留作当前
交互证据，但不会进入自动长期候选。

可选模型模式：

```env
MEMORY_EXTRACTION_MODE=shadow
MEMORY_MODEL_BASE_URL=https://model-gateway.example.com/v1
MEMORY_MODEL_API_KEY=...
MEMORY_EXTRACTION_MODEL=memory-extractor
```

模型必须返回结构化候选；输出经过 Pydantic、来源、敏感性、层级、TTL 和投毒策略复核，
模型没有数据库写权限。`shadow` 把通过门控的模型候选写为 `quarantined`，不参与召回；可用
`GET /v1/memory/items?statuses=quarantined` 审核和建立金标。审核达到业务阈值后，`hybrid`
合并确定性规则和模型结果，`model` 仅使用模型结果。

## 数据生命周期

- PostgreSQL 是唯一事实源；
- 新事件和 extraction outbox 在同一事务提交；
- worker 使用 `FOR UPDATE SKIP LOCKED`、租约恢复和最多五次指数退避；
- 删除先把记忆同步标为 `deleting`，随后 worker 删除记忆和证据，并把指定原始事件擦成
  不可召回 tombstone；保留 payload hash 是为了阻止同一幂等请求把已删内容复活；
- 到期记忆转为 `expired`，不会参与召回；
- 原始事件超过保留期后擦除正文和 metadata，但保留最小证据壳与 ID；
- 完成或失败的 outbox 记录七天后清理；
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
migration owner，也不要把这些默认密码带到共享或生产环境。

完全自管 PostgreSQL 时，先以管理员创建 `memory` 数据库，执行
`deploy/postgres/init.sql` 创建本地角色，再以 owner URL 执行 Alembic；随后把 `.env` 恢复为
API/worker 的受限 URL。Linux/macOS 使用 `.venv/bin/`。

## 测试

```bash
pytest -q
ruff check src migrations tests
```

真实数据库测试要求 `MEMORY_TEST_DATABASE_URL` 指向名称以 `_test` 结尾的可丢弃数据库，
测试会自动执行 migration：

```bash
set MEMORY_TEST_DATABASE_URL=postgresql+psycopg://.../memory_test
pytest -q -m postgres
```

## 生产部署前必须替换

- 将 `MEMORY_ENVIRONMENT` 设为 `production`，删除开发 JWT secret，配置真实 JWKS；
- 使用 KMS/Vault 或编排平台 secret，不把数据库和模型凭证写进镜像；
- 为 owner、API、worker 使用独立密码和最小权限；
- 在网关或反向代理设置请求体上限；应用层字段上限不能避免服务器先缓冲超大 HTTP body；
- 配置 PostgreSQL 备份、PITR、TLS、连接池、集中日志和告警；
- 用真实业务金标评估误存、漏存、跨用户隔离、删除完成率和召回质量；
- 在启用模型抽取前运行 shadow mode，并由产品、安全和法务确认敏感类别与保留期。

第一版有意不包含 Redis、Kafka、Celery、Neo4j、独立向量数据库或自动 L4。当前召回使用
PostgreSQL FTS、中文 trigram、L3 画像常驻和场景化 L1；数据证明需要后再启用 embedding。
