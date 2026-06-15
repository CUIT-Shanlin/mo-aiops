# mo-chat-aiops · M0 地基层 详细设计（Spec）

> **里程碑**：M0 地基（对齐 `docs/aiops_overview_design.md` §5 Roadmap）
> **版本**：v1.0 · 2026-06-15
> **配套**：概要 `aiops_overview_design.md`、需求 `aiops_requirements.md`、开发指南 `AGENTS.md`、Keep 借鉴 `keep_architecture.md`

---

## 1. 目标与范围

### 1.1 M0 目标

搭建 `mo-chat-aiops` 后端的地基层：`app/core/` 全套基础设施 + FastAPI 骨架 + 统一响应包装 + Alembic 启动自动迁移 + 本地 docker 依赖（PostgreSQL/Redis）+ 测试骨架。为 M1（抽象层/Provider/projects CRUD）提供稳定接口。

### 1.2 验收标准（对齐 Roadmap M0）

- `uv run uvicorn app.main:app` 能起服务
- `/health` 真实连通 DB + Redis
- JWT 校验通过（合法 admin token 过；非法/过期/非 admin 拒绝）
- `/metrics` 与健康检查可访问
- `uv run pytest` 全绿

### 1.3 关键决策（已与用户确认）

| 项 | 决策 |
|---|---|
| `project_context` | M0 **仅校验 X-Project-Id 存在 + UUID 格式**，缺失/非法返回 `{code:4001}`；不查库（行级库校验留 M1） |
| Alembic | 搭 async 机制 + **首个迁移建 `projects` 表** + 启动自动 `upgrade head` |
| JWT | **HS256**，与 Java 主服务共享对称 Secret，校验 `exp` + payload `role=admin` |
| `/metrics` | **占位端点**（基础 Prometheus 文本），完整业务指标留 M6 |
| DB/Redis | **真实连通**，`docker/docker-compose.yml` 提供本地实例 |

---

## 2. 从 Keep 的借鉴取舍（基于源码研究）

| 借鉴（照搬模式） | 规避（Keep 的弱项/同步实现） |
|---|---|
| `get_app()` 工厂建 app，便于测试注入 | engine 模块级全局 + fork 补丁 → 我们在 **lifespan 内**建 async engine，`await engine.dispose()` 关闭 |
| `Depends(get_auth_verifier(scopes))` 鉴权依赖形态 + PyJWT HS256 解码 | 缺 secret 只 warning → 我们**缺 `JWT_SECRET` 直接启动失败** |
| 鉴权实体携带身份（tenant_id → 我们的 user_id/role） | 多 auth 类型 importlib 工厂 → M0 只留 Bearer JWT |
| `models/db`(ORM) 与 `schemas`(DTO) 分离、`core/` 基础设施分层 | 5972 行巨型 db.py → 我们按领域拆 repository |
| Alembic「import 所有 model 进 metadata」+ 代码内 `command.upgrade(head)` + 绝对路径修正 `script_location` | `render_as_batch`（仅 SQLite 需要）、半吊子 async env → 用标准 async env 模板 |
| `dictConfig` + JSON formatter + dev/prod 双 formatter | 写 DB 的重型 handler、爬栈取 trace_id → 用 **contextvars** 注入 project_id/trace_id |
| JSON 序列化 `default=str` 解决 datetime（用于 JSONB） | starlette `Config` + 散落 `os.environ` → 用 **pydantic-settings 单一 Settings** |

**比 Keep 做得更好**：Keep 租户隔离靠每个 db 函数手写 `.where(tenant_id==)`，易漏。我们 M0 先把 `project_id` 隔离基础设施铺好（repositories 层统一携带，M1 接入真实查询），从源头防跨项目泄漏。

---

## 3. 目录结构（M0 实建部分）

```
app/
  __init__.py
  main.py                # get_app() 工厂 + lifespan + 路由挂载 + 异常处理注册
  core/
    config.py            # pydantic-settings Settings（env，@lru_cache 单例）
    constants.py         # ErrorCode/RedisKey 模板等常量单点声明
    logging.py           # dictConfig + JSON formatter + contextvars 注入
    db.py                # async engine + async_sessionmaker + get_session 依赖
    redis.py             # redis.asyncio 连接池 + key 命名空间拼接
    security.py          # PyJWT HS256 鉴权依赖（role=admin）
    project_context.py   # X-Project-Id 解析依赖（M0 仅格式校验）
  schemas/
    __init__.py
    response.py          # 统一响应包装 + APIError + 分页模型
  models/
    __init__.py
    base.py              # DeclarativeBase（Alembic metadata 源）
    project.py           # Project ORM（projects 表）
  repositories/
    __init__.py
    base.py              # ProjectScopedRepository 隔离基类（M0 立基类，M1+ 继承）
  api/
    __init__.py
    health.py            # /health + /metrics 占位 + /api/whoami 探针
  db/
    __init__.py
    migrate.py           # 代码内 alembic upgrade head 入口（lifespan 调用）
    migrations/          # Alembic：env.py + versions/0001_create_projects.py
docker/
  docker-compose.yml     # postgres + redis
  .env.example           # DATABASE_URL/REDIS_URL 默认指向本地 compose 服务
tests/
  __init__.py
  conftest.py            # async fixtures：engine、AsyncClient、签 JWT 工具
  test_config.py test_security.py test_project_context.py
  test_response.py test_health.py test_whoami.py
alembic.ini
.env.example
```

---

## 4. 模块详细设计

### 4.1 `core/config.py`

pydantic-settings `Settings`（`@lru_cache` 单例），`.env` 加载，回退环境变量。

字段：
- `database_url: str`（必填）— asyncpg DSN，如 `postgresql+asyncpg://...`
- `redis_url: str`（必填）；`redis_db: int = 1`
- `jwt_secret: str`（必填，缺失启动失败）；`jwt_algorithm: str = "HS256"`
- `java_service_internal_token: str | None = None`
- `datasource_secret_key: str | None = None`（M0 留位，M1 接入 cryptography 解密数据源凭证时启用）
- `llm_provider/llm_api_key/llm_base_url/llm_model`（M0 可选，留位）
- `cors_origins: Annotated[list[str], NoDecode] = ["*"]`（dev）；`NoDecode` 关掉 pydantic-settings 对复杂类型的 JSON 预解码 + `field_validator` 拆逗号分隔字符串（如 `CORS_ORIGINS=*`）。**不能只用 validator**：复杂类型在 source 层先被 JSON 解码，裸 `*` 会在 validator 跑之前就抛错。
- `log_format: Literal["dev","json"] = "json"`
- `environment: Literal["dev","prod"] = "dev"`

约束：敏感字段（jwt_secret/llm_api_key/java token/datasource_secret_key）不得进日志。

### 4.2 `core/constants.py`

常量单点声明（M0 范围）：
- `class ErrorCode(IntEnum)`：`SUCCESS=0`、`INVALID_PROJECT=4001`、`UNAUTHORIZED=4010`、`FORBIDDEN=4030`、`VALIDATION_ERROR=4220`、`INTERNAL=5000`。**分段规约**（后续按域扩展，避免拍脑袋编号）：`40xx`=鉴权/上下文、`41xx`=告警域、`42xx`=自愈域/请求校验、`43xx`=Agent 域、`5xxx`=系统。
- `SERVICE_NAME = "mo-chat-aiops"`
- `class RedisKey`：key 后缀**单点常量**（`AGENT_STATUS`/`INGEST`/`ALERTS`/`TOPOLOGY_CACHE`/`CONFIG`/`WS_AGENT`/`WS_HEAL`/`RECENT_ERRORS` 等，后续里程碑登记于此）+ `RedisKey.of(project_id, suffix)` 拼接 `aiops:{project_id}:{suffix}`。调用点引用常量，禁散落字面量（落地「key 模板单点声明」硬约束）。

### 4.3 `core/logging.py`

- `logging.config.dictConfig` 配置；JSON formatter（python-json-logger 风格，固定附 `service=mo-chat-aiops`）；`log_format=dev` 时人类可读。
- `contextvars`：`trace_id_var`、`project_id_var`；formatter 从 contextvars 读取注入每条日志。
- `setup_logging(settings)` 入口，main 启动时调用。

### 4.4 `core/db.py`

- `create_engine(settings)`：`create_async_engine(database_url, pool_size=5, max_overflow=15, pool_pre_ping=True, json_serializer=<dumps default=str>)`。
- `make_sessionmaker(engine)`：`async_sessionmaker(engine, expire_on_commit=False)`。
- engine/sessionmaker 存 `app.state`，**lifespan 内创建**，不放模块顶层全局。
- `get_session(request) -> AsyncIterator[AsyncSession]`：FastAPI 依赖，从 `request.app.state.sessionmaker` 取，`async with` yield。
- `async def ping_db(engine) -> bool`：`SELECT 1` 探活，异常返回 False。

### 4.5 `core/redis.py`

- `create_redis(settings)`：`redis.asyncio.from_url(redis_url, db=redis_db, decode_responses=True)` + 连接池。
- 存 `app.state.redis`，lifespan 建/关（`await redis.aclose()`）。
- `get_redis(request)`：FastAPI 依赖。
- `async def ping_redis(redis) -> bool`：`PING` 探活，异常返回 False。

### 4.6 `core/security.py`

- `get_current_user`：FastAPI 依赖。
  - 从 `Authorization: Bearer <token>` 取 token；缺失 → `APIError(UNAUTHORIZED, 4010)`。
  - `jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp"]})`，强制要求并校验 `exp`（不带 exp 的 token 永不过期，须拒绝）；`InvalidTokenError`/`ExpiredSignatureError` → 4010。
  - 校验 payload `role == "admin"`，否则 → `APIError(FORBIDDEN, 4030)`。
  - 返回 `CurrentUser(user_id=payload.get("sub"), role="admin")`（pydantic model）。
- 缺 `JWT_SECRET` 在 Settings 加载期即失败，不到运行期。

### 4.7 `core/project_context.py`

- 纯函数 `resolve_project_id(raw) -> str`：校验存在 + UUID 格式，缺失/非法抛 `APIError(INVALID_PROJECT, 4001)`。**纯函数与依赖分离**，便于 M5 WebSocket 从 `?project_id=` 取值后直接复用（同理 `security.decode_and_verify` 供 WS `?token=` 复用）。
- `get_project_id`：FastAPI 依赖。从 header `X-Project-Id` 取 → 调 `resolve_project_id` → `set_project_id` 注入日志 contextvar → 返回。
  - WS 路径复用 `resolve_project_id` 时需自行补 `set_project_id`/`set_trace_id`（依赖注入不覆盖 WS）。
  - M0 不查库；M1 接入 projects 仓库做存在性校验。

### 4.8 `schemas/response.py`

- `class APIError(Exception)`：携带 `code: int`、`message: str`、`detail: Any = None`、`http_status: int = 200`。
- `class ApiResponse(BaseModel, Generic[T])`：`{code:0, data}` 类型化信封，供路由声明 `response_model=ApiResponse[XxxData]`，让 FastAPI 自动生成 OpenAPI schema（契约即文档，AGENTS.md §9）。
- `class ErrorResponse(BaseModel)`：`{code, message, detail}` 失败信封。
- `def success(data: Any) -> dict`：便捷构造 `{"code": 0, "data": data}`。
- `class Page(BaseModel, Generic[T])`：`{total, page, size, items}`。
- 时间统一 ISO 8601 UTC（在序列化层约定，model 用 `datetime`，JSON encoder 输出 `...Z`）。

### 4.8a `repositories/base.py`（隔离基础设施）

- `class ProjectScopedRepository`：业务表仓库基类，构造期绑定 `session + project_id`，子类设 `project_column`，查询经 `scope(stmt)` 强制注入 `WHERE project_id`。让「漏写 project_id」在构造期不可能（兑现 §2 承诺）。M0 仅立基类 + 测试，M1+ 业务仓库继承。`projects` 表本身无 project_id，是唯一例外，不继承此基类。

### 4.9 `models/base.py` + `models/project.py`

- `base.py`：`class Base(DeclarativeBase): pass`（SQLAlchemy 2.0）。
- `project.py`：`class Project(Base)`，表名 `projects`：
  - `id: Mapped[uuid.UUID]`（PK，server_default `gen_random_uuid()` 或应用层生成）
  - `name: Mapped[str]`
  - `slug: Mapped[str]`（unique）
  - `enabled: Mapped[bool]`（默认 True）
  - `metric_profile: Mapped[str]`（默认 `"java"`）
  - `datasource_config: Mapped[dict[str, Any]]`（JSONB，默认 `{}`；加密细节留 M1，解密密钥经 `Settings.datasource_secret_key` 留位）
  - `created_at` / `updated_at: Mapped[datetime]`（server_default now / onupdate）
- M0 只建表结构，CRUD 业务留 M1。

### 4.10 `api/health.py`

- `GET /health`：无需鉴权。并发 `ping_db` + `ping_redis`；全通过 → `{code:0, data:{db:"ok", redis:"ok", status:"healthy"}}`；任一失败 → `data.status="degraded"` 标明哪个 down（仍 HTTP 200，便于探活区分）。
- `GET /metrics`：无需鉴权。M0 返回占位 Prometheus 文本（`# placeholder`，`text/plain`）。M6 接入真实指标。
- `GET /api/whoami`：受保护探针，`Depends(get_current_user)` + `Depends(get_project_id)`，返回 `success({"user_id":..., "role":"admin", "project_id":...})`，用于端到端验证鉴权+隔离+统一响应三链路。

### 4.11 `main.py`

- `get_app() -> FastAPI`：工厂。
- `lifespan`：
  1. `setup_logging(settings)`
  2. 建 async engine + sessionmaker → `app.state`
  3. 建 redis → `app.state`
  4. `run_migrations()`：代码内 `alembic.command.upgrade(config, "head")`（封装为 `app/db/migrate.py:run_upgrade_head`），`config.set_main_option("script_location", <abs path>)` 修正路径。**必须 `await asyncio.to_thread(run_upgrade_head)`**——env.py 在线模式用 `asyncio.run()` 建临时 loop，直接在 lifespan 运行中的事件循环里同步调用会抛 `RuntimeError`，丢到线程执行可规避并避免阻塞事件循环。
  5. `ping_db` / `ping_redis`（失败记 WARN，不阻断启动）
  6. `yield`
  7. `await engine.dispose()` + `await redis.aclose()`
- 中间件（注意 LIFO，后加先执行）：RequestId（注入 `trace_id_var`）→ CORS（`settings.cors_origins`）→ 日志。CORS 凭证：origins 含 `*` 时 `allow_credentials=False`（通配 origin 与 credentials 互斥，Starlette 在 `*` 下反射请求 origin，叠加 credentials 等于放任何站点带凭证跨域）；prod 须设显式 origins 才启用 credentials。
- 异常处理：`app.add_exception_handler(APIError, ...)` → `{code, message, detail}`（HTTP 200，业务码在 body）；`RequestValidationError` → `{code:4220, message, detail}`。
- 挂载 `api/health.py` 路由。

---

## 5. Alembic 设计

### 5.1 `db/migrations/env.py`（async 模板）

- `target_metadata = Base.metadata`；顶部 `import app.models.project`（确保所有表注册进 metadata，autogenerate 可见）。
- `run_migrations_online`：`create_async_engine` → `async with engine.connect() as conn: await conn.run_sync(do_run_migrations)`。
- `do_run_migrations(connection)`：`context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)` → `context.run_migrations()`。
- **不使用** `render_as_batch`（PG 不需要）。
- DB URL 从 `Settings` 读，不从 `alembic.ini` 硬编码。

### 5.2 首个迁移 `versions/0001_create_projects.py`

`op.create_table("projects", ...)` 建 §4.9 全部字段；`slug` 唯一索引；`id` UUID 主键。`downgrade` drop table。

### 5.3 启动自动迁移

`main.py` lifespan 内调 `command.upgrade(config, "head")`，幂等；多次启动安全。

---

## 6. docker/ 设计

### 6.1 `docker/docker-compose.yml`

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment: { POSTGRES_DB: mo_aiops, POSTGRES_USER: aiops, POSTGRES_PASSWORD: aiops }
    ports: ["5432:5432"]
    healthcheck: pg_isready -U aiops
    volumes: [pgdata:/var/lib/postgresql/data]
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck: redis-cli ping
volumes: { pgdata: {} }
```

### 6.2 `.env.example` / `docker/.env.example`

```
DATABASE_URL=postgresql+asyncpg://aiops:aiops@localhost:5432/mo_aiops
REDIS_URL=redis://localhost:6379
REDIS_DB=1
JWT_SECRET=change-me-shared-with-java
LOG_FORMAT=dev
ENVIRONMENT=dev
# 逗号分隔，Settings 的 field_validator 拆成 list
CORS_ORIGINS=*
```

---

## 7. 测试策略

pytest + pytest-asyncio + httpx.AsyncClient。集成测试（health/whoami）需 DB/Redis（docker-compose 起的实例；测试 env 指向本地）；纯单元测试不依赖 DB 在场。

| 测试文件 | 覆盖 |
|---|---|
| `test_config.py` | Settings 正常加载；缺 `JWT_SECRET` 抛错 |
| `test_constants.py` | ErrorCode 值；`RedisKey.of` 拼接 |
| `test_security.py` | HS256 验签通过；过期 token → 4010；非 admin → 4030；缺 token → 4010 |
| `test_project_context.py` | 缺 `X-Project-Id` → 4001；非 UUID → 4001；合法 UUID 通过 |
| `test_response.py` | `success()` 包装；`ApiResponse`/`ErrorResponse` 模型；`APIError`；`Page` 结构 |
| `test_repositories.py` | `ProjectScopedRepository.scope` 注入 `WHERE project_id`；构造绑定 |
| `test_models.py` | projects 表注册进 metadata + 字段齐全 |
| `test_migrations.py` | `run_upgrade_head`（经 `to_thread`）后 projects 表存在（asyncpg 断言） |
| `test_db.py` / `test_redis.py` | `ping_db`/`ping_redis` + session/客户端基本读写 |
| `test_logging.py` | JSON 日志含 `service` + `trace_id` |
| `test_health.py` | `/health` 真实连通 DB/Redis；`/metrics` 占位可访问 |
| `test_whoami.py` | 端到端：合法 admin + 合法 project → 200 `{code:0}`；缺任一 → 对应错误码 |
| `conftest.py` | async fixtures：会话级 `app_instance`（lifespan 跑一次）、`client`（用后 TRUNCATE/清 Redis）、`make_jwt`、autouse `_clear_settings_cache` 防 lru_cache 跨测试污染 |

**关键约束**：
- 迁移在 lifespan / async 测试里必须 `await asyncio.to_thread(run_upgrade_head)`，env.py 在线模式用 `asyncio.run()`，直接在运行中的 loop 调会 `RuntimeError`。
- 测试间隔离：`client` 依赖 `_clean_db_and_redis`，测试后 TRUNCATE projects + 清 `aiops:*` Redis key；`_clear_settings_cache` autouse 清 `get_settings` 缓存。

完成前跑 `uv run pytest`、`uv run ruff check .`、`uv run mypy app`。

---

## 8. 边界与非目标（M0 不做）

- 不实现 projects CRUD / 数据源连通性校验（M1）
- 不实现 project_context 的库存在性校验（M1）
- 不实现 Provider / Profile / 采集器 / Agent / 告警 / 自愈 / WebSocket（M2+）
- `/metrics` 不含真实业务指标（M6）
- 不实现非 admin 角色（全程仅 admin）
