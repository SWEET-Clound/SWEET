# 片段：基础设施、部署与迁移

> 覆盖 persistent-memory 的 11 个基础设施/配置文件：依赖与工具链、本地编排、镜像、数据库角色、环境变量样例、忽略规则、Alembic 配置与首个 DDL 迁移、以及对使用者的承诺。术语沿用《persistent_memory_close_reading.md》：原始事件=证据（L0），派生记忆=L1/L2/L3，outbox=后台抽取队列，RLS=行级安全。行号取自当前仓库；本片段只读代码，未运行 docker/alembic/pytest。

## 文件：persistent-memory/pyproject.toml（51 行）

**文件职责**：声明包元数据、运行时与测试依赖、四个命令行入口，以及 ruff / pytest 的项目级约定。

**逐段讲解**：
- `[build-system]`（pyproject.toml:1-3）：构建后端为 hatchling，`requires = ["hatchling"]` 无版本上限，构建阶段只装这一个工具。
- `name`/`version`/`description`/`readme`（pyproject.toml:6-9）：发行名 `persistent-memory`、0.1.0；`readme = "README.md"` 意味着 README 是打包输入，Dockerfile 必须 COPY 它。
- `requires-python = ">=3.12,<3.13"`（pyproject.toml:10）：只支持 3.12，与 Dockerfile 的 `python:3.12-slim`、ruff 的 `py312` 对齐；3.13 被显式排除。
- 11 个运行时依赖（pyproject.toml:11-23）全部 `==` 精确固定：alembic（迁移执行器）、fastapi（HTTP 层）、httpx（dev.py 冒烟与测试客户端）、prometheus-client（/metrics 端点）、`psycopg[binary,pool]`（PostgreSQL 驱动，binary 免编译、pool 供连接池）、pydantic 2.14.1（请求/领域模型）、pydantic-settings（`MEMORY_` 前缀的 Settings）、`PyJWT[crypto]`（JWKS/RS256 校验）、`sqlalchemy[asyncio]`（异步引擎，migrations/env.py 也用它的 engine_from_config）、structlog（结构化日志）、`uvicorn[standard]`（ASGI 服务器）。
- `[project.scripts]`（pyproject.toml:25-29）：`memory-api`→api:main、`memory-worker`→worker:main 是容器 CMD；`memory-dev-token`/`memory-smoke` 只给本地开发与冒烟使用。
- `[project.optional-dependencies].test`（pyproject.toml:31-36）：pytest 8.4.2、pytest-asyncio 0.26、ruff 0.17.3；README 的 `pip install -e ".[test]"` 就取这一组。
- `[tool.hatch.build.targets.wheel] packages = ["src/memory_system"]`（pyproject.toml:38-39）：src 布局映射，安装后 import 名为 `memory_system`；Dockerfile 也因此 COPY `src`。
- `[tool.ruff]`（pyproject.toml:41-43）：`target-version = "py312"`、`line-length = 100`，README 的 `ruff check src migrations tests` 用同一套规则。
- `[tool.pytest.ini_options]`（pyproject.toml:45-50）：`asyncio_mode = "auto"` 让 async 测试无需装饰器；`testpaths = ["tests"]`；注册 `postgres` marker，并在描述里写明它要求指向一次性 `*_test` 库。

**批注**：全部依赖精确 pin，可复现但没有哈希锁文件；`httpx` 是运行时依赖而非 test extra，原因是 dev.py 的 smoke 客户端要用。marker 描述只提 `MEMORY_TEST_DATABASE_URL`，但测试实际还读 `MEMORY_TEST_APP_DATABASE_URL`（tests/test_postgres_integration.py:35），该变量在 pyproject/README/.env.example 三处都未文档化（待确认是否遗漏）。

---

## 文件：persistent-memory/compose.yml（79 行）

**文件职责**：用 Docker Compose 描述「PostgreSQL + 一次性 Alembic 迁移 + API + worker」四段式本地栈与共享环境变量。

**逐段讲解**：
- `x-memory-env: &memory-env`（compose.yml:1-6）：YAML 锚点，被 migrate/api/worker 三个服务用 `<<: *memory-env` 展开；含 `MEMORY_ENVIRONMENT=development`（开启开发 token 路径）、`MEMORY_JWT_ISSUER`/`MEMORY_JWT_AUDIENCE`（与 dev token 对齐）、至少 32 字符的 `MEMORY_DEV_JWT_SECRET`、`MEMORY_EXTRACTION_MODE=heuristic`（无需模型即可跑通自动记忆）。
- `db` 服务（compose.yml:9-24）：`postgres:16.15-bookworm`；`POSTGRES_DB/USER/PASSWORD` 是 migration owner（`memory`/`memory-owner-local`），也是镜像初始化超级用户。
- `db.ports`（compose.yml:15-16）：`127.0.0.1:5432:5432` 只绑回环，避免局域网暴露数据库。
- `db.volumes`（compose.yml:17-19）：命名卷 `memory_db` 持久化 PGDATA；`deploy/postgres/init.sql` 以只读方式挂到 `/docker-entrypoint-initdb.d/001-roles.sql`，仅在数据目录为空时执行一次。
- `db.healthcheck`（compose.yml:20-24）：`pg_isready -U memory -d memory`，3s 间隔、3s 超时、20 次重试，是 migrate 的启动门槛。
- `migrate` 服务（compose.yml:26-37）：复用同一镜像，`command: ["alembic","upgrade","head"]`；`MEMORY_DATABASE_URL` 用 owner 连接串（migrations/env.py 会读取它覆盖 alembic.ini）；`depends_on: db: service_healthy`。
- `migrate.restart: "no"`（compose.yml:37）：一次性任务语义，失败不会被重启；api/worker 又依赖它成功，所以迁移失败会卡住整栈（不会带病启动）。
- `api` 服务（compose.yml:39-62）：`command: ["memory-api"]`；`MEMORY_DATABASE_URL` 指向受 RLS 限制的 `memory_app`；端口 `127.0.0.1:8080:8080`。
- `api.depends_on`（compose.yml:49-51）：`migrate: service_completed_successfully`，保证先建表再起 API。
- `api.healthcheck`（compose.yml:52-62）：用 `python -c` + 标准库 `urllib.request` 请求 `http://localhost:8080/health/ready`（镜像里没有 curl）；5s 间隔、3s 超时、20 次重试。
- `worker` 服务（compose.yml:64-75）：`command: ["memory-worker"]`；`MEMORY_DATABASE_URL` 用 `memory_worker`（`BYPASSRLS` 角色）；`restart: unless-stopped`。
- `worker.depends_on`（compose.yml:72-74）：同样等 migrate 成功；worker 无端口、无 healthcheck。
- `volumes: memory_db`（compose.yml:77-78）：唯一的持久化状态；README 的 `docker compose down -v` 就是在删它。

**批注**：三个服务都只用 `MEMORY_DATABASE_URL` 一个连接串（worker 走 `worker.py:95` 的 `worker_database_url or database_url` 回退），所以在这里看不到 `MEMORY_WORKER_DATABASE_URL`；同一份开发密钥同时出现在锚点与 .env.example，只适合本地。api 有 healthcheck 而 worker 没有，worker 挂死只能靠日志/进程状态发现（待确认是否接受）。

---

## 文件：persistent-memory/deploy/Dockerfile（16 行）

**文件职责**：把项目打成只运行、不含测试与构建缓存的 Python 3.12 镜像，默认入口是 API。

**逐段讲解**：
- `FROM python:3.12-slim`（deploy/Dockerfile:1）：唯一 stage，没有多阶段构建，也没有单独构建层；与 `requires-python` 的 3.12 对应。
- `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1`（deploy/Dockerfile:3-4）：不写 `__pycache__`、日志不缓冲，适合容器日志采集。
- `WORKDIR /app`（deploy/Dockerfile:6）：后续 COPY 与命令的基准目录，compose 里 `alembic` 的相对路径 `migrations` 也依赖此处。
- `COPY pyproject.toml README.md ./`（deploy/Dockerfile:7）：README 必须随包，因为 pyproject 的 `readme` 字段指向它。
- `COPY src ./src`（deploy/Dockerfile:8）：源码按 src 布局放入，配合 hatch 的 packages 配置。
- `COPY alembic.ini ./` 与 `COPY migrations ./migrations`（deploy/Dockerfile:9-10）：让同一镜像既能跑 API/worker，也能作为 migrate 容器执行 `alembic upgrade head`；不复制 tests，运行镜像因此不含测试。
- `RUN pip install --no-cache-dir .`（deploy/Dockerfile:11）：非 editable 安装；不装 `.[test]`，构建期需要网络。镜像自带包元数据但 `/app/src` 的源码与安装进 site-packages 的副本是两份。
- `USER 65532:65532`（deploy/Dockerfile:13）：以非 root 数字 uid/gid 运行（distroless 风格约定），容器内无对应用户名，/app 只读即可跑。
- `EXPOSE 8080`（deploy/Dockerfile:14）：仅文档/元数据；实际监听由 api.py:270-271 的 `host=0.0.0.0, port=8080` 决定。
- `CMD ["memory-api"]`（deploy/Dockerfile:15）：默认入口；compose 用 `command` 覆盖为 alembic / memory-worker。

**批注**：单 stage 让编译依赖（如有）进入运行镜像，目前依赖全是 wheel 且 psycopg 用 binary，所以可接受；但仓库没有 `.dockerignore`，构建上下文会包含 `.venv/`、`.git/`、缓存目录（待确认是否在 CI 里另有控制）。镜像未定义 `HEALTHCHECK`，健康检查完全由 compose 承担。

---

## 文件：persistent-memory/deploy/postgres/init.sql（4 行）

**文件职责**：PostgreSQL 首次初始化时创建两个非 owner 登录角色并授予连接权限，作为 RLS 隔离的角色基础。

**逐段讲解**：
- `CREATE ROLE memory_app ... NOSUPERUSER NOCREATEDB NOCREATEROLE`（deploy/postgres/init.sql:1）：API 专用角色，`LOGIN` + 固定密码；三个 NO 属性保证它既不能提权也不能建库建角色，且未给 `BYPASSRLS`，所以会受迁移里定义的 RLS 约束。
- `CREATE ROLE memory_worker ... BYPASSRLS`（deploy/postgres/init.sql:2）：后台任务角色，除同样无管理属性外显式 `BYPASSRLS`——worker 要跨全部租户处理 outbox 与删除任务，这是刻意的信任边界。
- `GRANT CONNECT ON DATABASE memory TO memory_app, memory_worker`（deploy/postgres/init.sql:3）：只给「能连上」，不给 schema/表权限；表级授权留到迁移里建表之后执行。

**批注**：密码硬编码在仓库文件里，属本地开发凭证（README 明确要求共享/生产前替换）。init.sql 由镜像 entrypoint 在**空数据目录**时执行一次，所以已存在的卷不会补建角色——这正是 README「旧版 Compose 建过同名卷需 `down -v`」的原因。此外角色 `memory`（owner）由 `POSTGRES_USER` 创建为超级用户，会自然绕过 RLS，因此「不要让 API 使用 owner」是硬要求而非建议。

---

## 文件：persistent-memory/.env.example（7 行）

**文件职责**：本地从源码运行时的环境变量样板，对应 Settings 的 `MEMORY_` 前缀与三种数据库角色。

**逐段讲解**：
- `MEMORY_ENVIRONMENT=development`（.env.example:1）：选择开发模式；生产模式会拒绝 dev secret 并要求 JWKS（settings.py:35-41）。
- `MEMORY_DATABASE_URL=...memory_app...@127.0.0.1:5432/memory`（.env.example:2）：API 连接串，受 RLS 限制；host 用 127.0.0.1 是因为走 compose 发布的回环端口，而 compose 内部服务间用 `db`。
- `MEMORY_WORKER_DATABASE_URL=...memory_worker...`（.env.example:3）：worker 的 `BYPASSRLS` 连接串；worker.py:95 优先读它，缺省回落 `MEMORY_DATABASE_URL`。
- `MEMORY_JWT_ISSUER=persistent-memory-dev` / `MEMORY_JWT_AUDIENCE=persistent-memory`（.env.example:4-5）：与 compose 锚点、dev token 签发端一致，否则验签会因 iss/aud 不匹配失败。
- `MEMORY_DEV_JWT_SECRET=...`（.env.example:6）：50 字符占位密钥，满足 settings.py:42-45 的 ≥32 字符校验；仅在 development 可用。
- `MEMORY_EXTRACTION_MODE=heuristic`（.env.example:7）：默认确定性抽取，不配置模型也能跑；切到 shadow/model/hybrid 时 pydantic 校验会强制要求 `MEMORY_MODEL_BASE_URL` 与 `MEMORY_EXTRACTION_MODEL`。

**批注**：Settings 用 `extra="forbid"`，任何未声明的 `MEMORY_*` 变量都会让进程启动失败，所以这份样板既是示例也是白名单：它没有覆盖 `MEMORY_JWKS_URL`、`MEMORY_MODEL_*`、`MEMORY_RAW_EVENT_RETENTION_DAYS` 等生产/调优键（待确认是否应有生产版样板）。文件中出现的明文密码与 secret 只能用于本地。

---

## 文件：persistent-memory/.gitignore（9 行）

**文件职责**：把本地密钥、虚拟环境、构建与缓存产物排除在版本库外。

**逐段讲解**：
- `.env`（.gitignore:1）：真实运行配置（含数据库密码与 JWT secret），由 `.env.example` 复制而来，绝不入库。
- `.venv/`（.gitignore:2）：README 里 `python -m venv .venv` 的本地虚拟环境。
- `__pycache__/`（.gitignore:3）：字节码目录；Dockerfile 另用 `PYTHONDONTWRITEBYTECODE` 从源头避免。
- `*.py[cod]`（.gitignore:4）：`.pyc/.pyo/.pyd` 编译产物，含跨平台残留。
- `.pytest_cache/`（.gitignore:5）：pytest 的 lastfailed/nodeids 缓存。
- `.ruff_cache/`（.gitignore:6）：ruff 缓存，README 的 lint 命令会生成。
- `.tmp-*/`（.gitignore:7）：忽略以 `.tmp-` 开头的目录，推测是测试/工具临时工作目录；仓库内未搜到生成它的代码（待确认）。
- `dist/`（.gitignore:8）：`python -m build` / hatch 的产物。
- `*.egg-info/`（.gitignore:9）：`pip install -e .` 生成的包元数据目录。

**批注**：只忽略精确的 `.env`，`.env.local`、`.env.production` 之类变体不会被忽略；也没有忽略 `.coverage`/`htmlcov/`、IDE 目录（与当前只跑 pytest+ruff 的工具链一致，属可接受的取舍）。

---

## 文件：persistent-memory/alembic.ini（40 行）

**文件职责**：Alembic 的入口配置：迁移脚本位置、DSN 占位、以及日志器/处理器/格式器声明。

**逐段讲解**：
- `script_location = migrations`（alembic.ini:2）：迁移脚本目录；Dockerfile 把它 COPY 到 `/app/migrations`，compose 的 migrate 服务因此可直接执行。
- `prepend_sys_path = src`（alembic.ini:3）：把 `src` 加入 `sys.path`，让迁移环境可 `import memory_system`；当前唯一迁移不 import 业务包，这条是为后续 autogenerate/脚本化迁移预留。
- `path_separator = os`（alembic.ini:4）：用操作系统分隔符解析多路径（新版 Alembic 取代 `version_path_separator`）。
- `sqlalchemy.url = postgresql+psycopg://memory:memory@localhost:5432/memory`（alembic.ini:5）：兜底 DSN，密码 `memory` 与 compose 的 `memory-owner-local` 不一致——只在未设置 `MEMORY_DATABASE_URL` 时生效，实际部署永远被 env.py 覆盖。
- `[loggers]/[handlers]/[formatters]` 的 `keys`（alembic.ini:7-14）：声明 root、sqlalchemy、alembic 三个 logger，console 一个 handler，generic 一个 formatter。
- `[logger_root]`（alembic.ini:16-19）：WARN + console handler，是未单独配置记录器的兜底。
- `[logger_sqlalchemy]`（alembic.ini:21-24）：WARN 且 `handlers =` 为空 → 向 root 冒泡，避免迁移时刷 SQL。
- `[logger_alembic]`（alembic.ini:26-29）：INFO，能看到 run/upgrade 进度。
- `[handler_console]`（alembic.ini:31-35）：StreamHandler 输出到 stderr，级别 NOTSET（由 logger 决定）。
- `[formatter_generic]`（alembic.ini:37-39）：`levelname-5.5s [name] message` + `%H:%M:%S`，与容器日志风格接近。

**批注**：整份 ini 的日志配置只有在 `config.config_file_name` 存在时才会被 `fileConfig` 应用（migrations/env.py:8-9）；DSN 默认值里的密码是明显的占位/过期值，属易误导点（不影响 compose，但手工 `alembic` 时容易踩）。

---

## 文件：persistent-memory/migrations/env.py（42 行）

**文件职责**：Alembic 运行时环境：读取配置、用环境变量覆盖 DSN、并分别实现 offline/online 两种执行路径；全部迁移以裸 SQL 执行。

**逐段讲解**：
- 导入（migrations/env.py:1-5）：标准库 os/`fileConfig` + `alembic.context` + `engine_from_config,pool`，没有导入业务模型。
- `config = context.config` + `fileConfig(...)`（migrations/env.py:7-9）：把 alembic.ini 的日志配置装载进 logging。
- `MEMORY_DATABASE_URL` 覆盖 DSN（migrations/env.py:11-13）：直接 `os.getenv`，与 Settings 使用同一个环境变量名；compose 的 migrate 服务正是靠这点以 owner 身份执行迁移。
- `run_migrations_offline`（migrations/env.py:16-23）：不连接数据库，`literal_binds=True` + named paramstyle 生成可审阅的 SQL 脚本（`alembic upgrade head --sql`），适合人工复核 DDL。
- `run_migrations_online`（migrations/env.py:26-31）：`engine_from_config` + `poolclass=NullPool`，迁移期间不保留连接池，用完即断。
- `context.configure(connection=...)` 与 `begin_transaction`（migrations/env.py:32-35）：单连接、单事务执行整批迁移，中途失败整体回滚（取决于 DDL 的事务性，PostgreSQL 支持事务 DDL）。
- offline/online 分派（migrations/env.py:38-41）：按 `context.is_offline_mode()` 选择路径。

**批注**：没有传 `target_metadata`（也没有 `compare_type`/`render_as_batch`），所以本仓库无法用 autogenerate 产出迁移，全部 DDL 靠手写 `op.execute`——与首个迁移的写法一致。DSN 走原始环境变量而非 pydantic Settings，因此不会有 `SecretStr` 保护与 `extra="forbid"` 校验，错误连接串只会以驱动异常的形式暴露。

---

## 文件：persistent-memory/migrations/script.py.mako（25 行）

**文件职责**：`alembic revision` 生成新迁移文件时使用的 Mako 模板。

**逐段讲解**：
- 文档字符串（migrations/script.py.mako:1-6）：写入 `${message}`、`Revision ID`、`Revises`、`Create Date`，是迁移文件的可读索引。
- 导入段（migrations/script.py.mako:7-11）：固定 `from alembic import op`、`import sqlalchemy as sa`，并用 `${imports if imports else ""}` 为 autogenerate 追加的额外 import 留槽（常被 `import sqlalchemy.dialects.postgresql` 用到）。
- 修订标识（migrations/script.py.mako:13-16）：`revision`/`down_revision`/`branch_labels`/`depends_on` 四个模块级变量，供 Alembic 构建版本链。
- `upgrade()`（migrations/script.py.mako:19-20）：`${upgrades if upgrades else "pass"}`，无操作时生成 `pass`。
- `downgrade()`（migrations/script.py.mako:23-24）：`${downgrades if downgrades else "pass"}`，保证回滚可写。

**批注**：仓库唯一的迁移文件 `20261029_0001_initial.py` 并未遵循此模板：它没有 `from typing import Sequence, Union`、没有类型注解、也没有 `sa` 导入，而是全手写 `op.execute`。也就是说模板只对**未来生成的**迁移生效，两类文件风格会不一致（不影响执行）。

---

## 文件：persistent-memory/migrations/versions/20261029_0001_initial.py（244 行）

**文件职责**：第一版完整 schema：7 张表、7 个索引（3 个部分索引，其中 1 个唯一）、6 张表的同构 RLS 策略 + 1 张关联表的 EXISTS 策略、以及 app/worker 两个角色的授权；全部用 `op.execute` 裸 SQL 表达。

**逐段讲解**：
- 文件头与修订标识（20261029_0001_initial.py:1-12）：`revision="20261029_0001"`、`down_revision=None`（基线迁移）、无 branch/depends，因此它是整条版本链的起点。
- `CREATE EXTENSION IF NOT EXISTS pg_trgm`（:16）：为中文/模糊匹配的 trigram 索引（:156）提供 `gin_trgm_ops`；PG13+ 中 pg_trgm 属 trusted extension，数据库 owner 即可安装，compose 的 migrate 用超级用户连接故无障碍。
- `memory_event` 表（:19-37）：原始事件（L0 证据）。`id` 为应用生成的 uuid 主键；`tenant_id/user_id/agent_id/thread_id` 全 NOT NULL 界定作用域；`idempotency_key` + `payload_hash` 支撑幂等；`source` 用 CHECK 限定 5 种来源；`content/occurred_at/recorded_at` 区分「发生时间」与「入库时间」；`metadata jsonb`、`prohibited_from_memory`（禁止记忆标记）、`redacted_at`（擦除墓碑）；`UNIQUE (tenant_id,user_id,agent_id,idempotency_key)` 是幂等的数据库落点（跨 thread 仍唯一，thread_id 不参与）。
- `memory_item` 表（:40-67）：派生记忆（L1/L2/L3）。`layer` CHECK 四层（L4 结构上允许但自动写入关闭）；`kind` 自由文本；`status` CHECK 五态 `active/superseded/deleting/quarantined/expired`；`confidence` 双重 CHECK 落在 [0,1]；`policy_version` 记录生成策略版本；`valid_from/valid_to` 双时态有效区间、`expires_at` TTL；`supersedes_id` 自引用 FK 表达「更正而非覆盖」（未加 ON DELETE，默认 NO ACTION）；`created_at/updated_at` 默认 now() 但无触发器维护 `updated_at`（由应用写入）；`deleted_at` 软删时间；`fingerprint` 供去重；`future_utility/durability` 各带 CHECK 枚举；`affect jsonb` 存情绪辅助信号。
- `search_vector` 生成列（:62-64）：`GENERATED ALWAYS AS (to_tsvector('simple', coalesce(statement,''))) STORED`，用 `simple` 词典避免英文词干化误伤中文，全文检索列随写自动维护。
- `uq_memory_item_fingerprint`（:70-73）：`(tenant_id,user_id,agent_id,fingerprint)` 上的**部分**唯一索引，`WHERE fingerprint IS NOT NULL`，只对可计算指纹的记忆去重（postgres.py:280 的 `ON CONFLICT` 依赖它）。
- `memory_evidence` 表（:77-82）：记忆↔事件多对多关联，复合主键 `(memory_id,event_id)`，两侧外键都 `ON DELETE CASCADE`；本表**没有**租户列，隔离靠 :187-210 的 EXISTS 策略。
- `memory_outbox` 表（:86-101）：抽取任务队列。`id bigserial` 提供全序；`event_id` FK CASCADE；scope 三列冗余存储便于按租户清理；`kind` CHECK 目前只有 `extract`；`status` 五态；`attempts/available_at/locked_at/completed_at/error_code` 覆盖重试与租约；`memory_event` 与本表在应用同一事务内写入（见 postgres.py:81-112）。
- `deletion_job` 表（:105-123）：物理删除任务。`id uuid` 主键由应用生成；`memory_ids uuid[]`/`event_ids uuid[]` 用数组批量携带目标，默认空数组；`status` 五态、`attempts/available_at/locked_at/error_code/created_at/completed_at` 与 outbox 同构（便于复用同一套 claim/backoff 语义）。
- `command_dedup` 表（:126-138）：显式命令（记住/纠正/忘记/禁止）的幂等账本，主键 `(tenant_id,user_id,agent_id,idempotency_key)`；`payload_hash` 区分「同键同请求」与「同键不同请求」；`status CHECK ('processing','completed')` + `receipt jsonb` 缓存原结果。
- `memory_audit` 表（:142-152）：审计流水，`action`/`target_id text` 自由记录；无 updated_at，语义上应只追加。
- `ix_memory_event_scope_time`（:153）：`(tenant_id,user_id,agent_id,occurred_at DESC)`，支撑按 scope 倒序取最近事件与召回的时间过滤。
- `ix_memory_item_scope_status`（:154）：`(scope, status, updated_at DESC)`，是 `GET /v1/memory/items?statuses=` 一类列表/召回的主索引。
- `ix_memory_item_search`（:155）：`USING gin (search_vector)`，全文检索。
- `ix_memory_item_trigram`（:156）：`USING gin (statement gin_trgm_ops)`，为中文子串/LIKE 兜底，依赖 :16 的扩展。
- `ix_memory_outbox_ready`（:157）：`(available_at, id) WHERE status IN ('pending','retry')`，worker claim（`FOR UPDATE SKIP LOCKED`）的候选索引；租约恢复分支（processing + locked_at 超 5 分钟）不在此部分索引覆盖范围内。
- `ix_deletion_job_ready`（:158-161）：`(available_at, created_at) WHERE status IN ('pending','retry')`，删除任务的同类候选索引。
- RLS 开关与同构策略（:163-186）：对 `memory_event`、`memory_item`、`memory_outbox`、`deletion_job`、`command_dedup`、`memory_audit` 六表循环执行 `ENABLE ROW LEVEL SECURITY`，并按 `{table}_scope` 模板各建一条策略：`memory_event_scope`、`memory_item_scope`、`memory_outbox_scope`、`deletion_job_scope`、`command_dedup_scope`、`memory_audit_scope`；`USING` 与 `WITH CHECK` 都用 `tenant_id = current_setting('app.tenant_id', true) AND user_id = ... AND agent_id = ...`，即三列必须同时匹配当前事务的 scope。
- `current_setting(..., true)` 的语义（:176-184）：第二个参数是 `missing_ok`，未设置的 GUC 返回 NULL 而非报错；比较结果为 NULL 时策略不通过，等于**失败关闭**（没设 scope 就既看不到也写不进，postgres.py:1011-1018 用 `set_config(...,true)` 在每个事务内设置）。
- `memory_evidence` 的 RLS（:187-210）：单独 `ENABLE` + `memory_evidence_scope` 策略，用 `EXISTS (SELECT 1 FROM memory_item m WHERE m.id=memory_id AND m.tenant_id=... )` 把无租户列的证据表绑定到所属记忆的作用域，`USING`/`WITH CHECK` 各写一遍（读可见性与写入校验都要管）。
- 授权 `DO` 块（:211-233）：整段用 `IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=...)` 守卫，角色不存在就跳过，使迁移可在没有 init.sql 的环境（CI/托管 PG）单独执行。
- `memory_app` 授权（:215-224）：`USAGE ON SCHEMA public`；`SELECT, INSERT, UPDATE` 给 6 张业务表；`memory_outbox` 只给 `SELECT, INSERT`（API 能入队、不能自己 claim/改状态）；`DELETE ON command_dedup`（清理幂等账本）；`USAGE ON SEQUENCE memory_outbox_id_seq`（bigserial 插入必需）。
- `memory_worker` 授权（:225-230）：`USAGE ON SCHEMA public`；`SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public`；`USAGE, SELECT ON ALL SEQUENCES`——worker 的全权来自角色层面的 `BYPASSRLS`（init.sql:2）。
- `downgrade()`（:236-243）：按依赖逆序 `DROP TABLE IF EXISTS`：memory_audit → command_dedup → deletion_job → memory_outbox → memory_evidence → memory_item → memory_event；`IF EXISTS` 让半途失败的重试可继续。

**批注**：
1. 授权与「审计只追加」有张力：:217-220 把 `UPDATE` 一并授给了 `memory_audit`（也授给了 `deletion_job`/`command_dedup`，这两者需要状态流转）；API 端是否真的永不改审计行，本片段未核（待确认）。
2. `memory_evidence` 只有 `(memory_id,event_id)` 主键，没有 `event_id` 单列索引，按事件查证据/级联删除的扫描代价待确认。
3. `GRANT ... ON ALL TABLES IN SCHEMA public` 是**执行时快照**，未来迁移新建的表不会自动授权，需要新迁移里重新 GRANT（待确认是否有此约定）。
4. RLS 只 `ENABLE` 未 `FORCE`，表 owner（migration owner / 超级用户）天然绕过；`memory_evidence` 策略内的 EXISTS 子查询访问 `memory_item` 时，后者自己的 RLS 也会生效，因共用同一组 `app.*` 设置而结论一致（多一层过滤，规划开销待确认）。
5. `downgrade()` 不撤销 `pg_trgm` 扩展、不 revoke 授权，属「只回滚表结构」的取舍；且会丢数据。
6. `memory_item.updated_at` 无触发器，依赖应用显式更新；`:157` 部分索引与 claim 查询的形态匹配度、`:158-161` 同理，属可优化点而非错误。

---

## 文件：persistent-memory/README.md（148 行）

**文件职责**：对使用者的正式承诺：如何五分钟跑起来、API 契约、自动记忆与删除语义、本地开发与测试方式、上线前必须替换的清单，以及第一版刻意不做什么。

**逐段讲解**：
- 定位（README.md:1-4）：可运行的独立记忆服务；原始交互存为证据，后台 worker 生成受策略约束的 L1/L2/L3，对外提供稳定 HTTP API——与 DDL 中的 `memory_event` / `memory_item` / `memory_outbox` 表结构互为印证。
- 五分钟启动·前置与命令（README.md:6-14）：要求 Docker Engine/Desktop 且支持 `docker compose`；`up --build -d` → `ps` → 在 api 容器内跑 `memory-smoke`，承诺冒烟链路为 event→自动记忆→召回→删除。
- 五分钟启动·成功标志（README.md:16-20）：最后一行固定输出 `smoke ok: event -> automatic memory -> recall -> deletion`（dev.py:119 的原文），可作为脚本断言。
- 服务地址（README.md:22-26）：API 8080、OpenAPI `/docs`、PostgreSQL `localhost:5432`，与 compose 的回环端口映射一致。
- 启动顺序与三角色（README.md:28-30）：PostgreSQL → Alembic migration → API 与 worker；三个隔离角色分别是 migration owner、受 RLS 限制的 API 角色、仅供后台任务的 `BYPASSRLS` worker 角色——这是 compose.yml 与 init.sql 的设计目的。
- 旧卷警告（README.md:32-33）：旧版 Compose 建过同名卷需 `docker compose down -v`，并明确警告该命令会删掉本地开发数据、不能用于需保留数据的环境。
- 开发 token（README.md:37-42）：只在 `MEMORY_ENVIRONMENT=development` 且 `MEMORY_DEV_JWT_SECRET` ≥32 字符时可签发，命令 `memory-dev-token`；生产模式禁止开发密钥、强制 JWKS（settings.py:33-50 是这条承诺的实现）。
- 信任边界（README.md:44-45）：token 里的 `tenant_id`/`user_id`/`agent_id`/`memory_purposes`/`memory_write` 是可信授权来源，请求正文不能扩大 scope——对应 API 层从 token 取 Identity 而非读 body。
- 核心 API 清单（README.md:47-55）：7 个端点：记录事件、构建上下文、查事件抽取状态、显式命令、列记忆/证据、查删除任务、两个健康检查；与 DDL 的 delete/outbox 表一一对得上。
- 幂等承诺（README.md:57-58）：所有写请求必须带 `Idempotency-Key`；同键同 payload 返回原结果，同键不同 payload 返回 409——实现是 `memory_event` 的唯一约束 + `payload_hash` 比较（postgres.py:91-135）。
- 自动记忆行为·heuristic 规则（README.md:60-70）：默认无需模型；明确「请记住 X」且过敏感门控 → L3；带时间边界的会议/预约/截止/提醒 → L2 默认 180 天到期；单次高唤醒负面情绪 → L1 24 小时到期；同一事件可同时产出 L1+L2；assistant/tool/web 不能自动创建用户事实；凭证/秘密/高敏感/提示注入被拒；L4 自动写入首版关闭。
- memory_write 开关（README.md:72-73）：自动抽取仅在签名 token 含 `memory_write=true` 时启用；关闭后事件仍可作为当前交互证据保留但不进长期候选（对应 postgres.py:100 的 `allow_memory_extraction`）。
- 可选模型模式（README.md:75-87）：shadow/model/hybrid 三种模式的环境变量样例；模型必须返回结构化候选，输出再经 Pydantic、来源、敏感性、层级、TTL 与投毒策略复核，模型没有数据库写权限；shadow 把通过门控的候选写成 `quarantined` 且不参与召回，用 `GET /v1/memory/items?statuses=quarantined` 审核建金标；达阈值后用 hybrid，最终可只用 model。
- 数据生命周期（README.md:89-99）：8 条承诺：PostgreSQL 唯一事实源；事件与 outbox 同事务提交；worker 用 `FOR UPDATE SKIP LOCKED` + 租约恢复 + 最多五次指数退避；删除先标 `deleting`、worker 删记忆与证据、把指定原始事件擦成不可召回 tombstone、保留 payload hash 防「同幂等请求复活已删内容」；到期转 `expired`（run_maintenance 实现）；原始事件过保留期擦正文与 metadata 但留最小证据壳；完成/失败的 outbox 七天后清理；上下文用 `<memory-data trust="untrusted">` 渲染、不能变成 system instruction。
- 本地源码运行（README.md:101-120）：需 Python 3.12；推荐 `docker compose up -d db migrate` 只起库与迁移，再从源码起 API/worker；venv + `pip install -e ".[test]"` + `copy .env.example .env` + `memory-api`，另开终端 `memory-worker`；强调 `.env.example` 仅含本地凭证、API 用 `memory_app`、worker 用 `memory_worker`、不要让 API 用 owner、不要把默认密码带到共享/生产；完全自管 PG 时先建库→跑 init.sql→以 owner URL 执行 Alembic→把 .env 换回受限 URL；Linux/macOS 用 `.venv/bin/`。
- 测试（README.md:122-135）：`pytest -q` 与 `ruff check src migrations tests`；真实库测试要求 `MEMORY_TEST_DATABASE_URL` 指向名称以 `_test` 结尾的可丢弃库，测试会自动执行 migration；`pytest -q -m postgres` 运行。
- 生产部署前必须替换（README.md:137-145）：7 条清单：`MEMORY_ENVIRONMENT=production` + 删 dev secret + 配 JWKS；KMS/Vault 或平台 secret，不把库/模型凭证写进镜像；owner/API/worker 独立密码与最小权限；网关层请求体上限（应用层字段上限挡不住服务器先缓冲超大 body）；备份/PITR/TLS/连接池/集中日志/告警；用真实金标评估误存漏存、跨租户隔离、删除完成率与召回质量；启用模型抽取前先跑 shadow 并由产品/安全/法务确认敏感类别与保留期。
- 第一版刻意不含（README.md:147-148）：无 Redis/Kafka/Celery/Neo4j/独立向量库/自动 L4；召回用 PG FTS + 中文 trigram + L3 画像常驻 + 场景化 L1，等数据证明需要后再启用 embedding——与 DDL 里没有向量列、只有 `search_vector` 和 trigram 索引完全一致。

**批注**：README 把「测试会自动执行 migration」写为承诺，集成测试里确有 `_upgrade_test_database`（tests/test_postgres_integration.py:45），但它只文档化了 `MEMORY_TEST_DATABASE_URL`，实际测试还读 `MEMORY_TEST_APP_DATABASE_URL`（同文件:35）用于 RLS 路径，属文档缺口。README 未说明 migrate 容器失败即阻塞 API/worker（compose 的 `service_completed_successfully` 语义），也未说明 worker 没有 healthcheck。`down -v` 的警告措辞清楚，是本地开发与数据保留之间的正确取舍。
