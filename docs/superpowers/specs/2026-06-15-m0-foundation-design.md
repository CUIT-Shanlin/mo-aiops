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
  api/
    __init__.py
    health.py            # /health + /metrics 占位 + /api/whoami 探针
  db/
    __init__.py
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
- `llm_provider/llm_api_key/llm_base_url/llm_model`（M0 可选，留位）
- `cors_origins: list[str] = ["*"]`（dev）
- `log_format: Literal["dev","json"] = "json"`
- `environment: Literal["dev","prod"] = "dev"`

约束：敏感字段（jwt_secret/llm_api_key/java token）不得进日志。

### 4.2 `core/constants.py`

常量单点声明（M0 范围）：
- `class ErrorCode(IntEnum)`：`SUCCESS=0`、`INVALID_PROJECT=4001`、`UNAUTHORIZED=4010`、`FORBIDDEN=4030`、`VALIDATION_ERROR=4220`、`INTERNAL=5000`
- `SERVICE_NAME = "mo-chat-aiops"`
- `def redis_key(project_id: str, suffix: str) -> str`：返回 `aiops:{project_id}:{suffix}`（单点拼接，禁散落硬编码）

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
  - `jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])`，校验 `exp`（PyJWT 默认校验）；`InvalidTokenError`/`ExpiredSignatureError` → 4010。
  - 校验 payload `role == "admin"`，否则 → `APIError(FORBIDDEN, 4030)`。
  - 返回 `CurrentUser(user_id=payload.get("sub"), role="admin")`（pydantic model）。
- 缺 `JWT_SECRET` 在 Settings 加载期即失败，不到运行期。

### 4.7 `core/project_context.py`

- `get_project_id`：FastAPI 依赖。
  - 从 header `X-Project-Id` 取（WS 场景另走 query，M0 不实现 WS）。
  - 缺失或非合法 UUID → `APIError(INVALID_PROJECT, 4001, "missing or invalid project")`。
  - 返回 `str(project_id)`。M0 不查库；M1 接入 projects 仓库做存在性校验。

### 4.8 `schemas/response.py`

- `class APIError(Exception)`：携带 `code: int`、`message: str`、`detail: Any = None`、`http_status: int = 200`。
- `def success(data: Any) -> dict`：`{"code": 0, "data": data}`。
- `class Page(BaseModel, Generic[T])`：`{total, page, size, items}`。
- 时间统一 ISO 8601 UTC（在序列化层约定，model 用 `datetime`，JSON encoder 输出 `...Z`）。

### 4.9 `models/base.py` + `models/project.py`

- `base.py`：`class Base(DeclarativeBase): pass`（SQLAlchemy 2.0）。
- `project.py`：`class Project(Base)`，表名 `projects`：
  - `id: Mapped[uuid.UUID]`（PK，server_default `gen_random_uuid()` 或应用层生成）
  - `name: Mapped[str]`
  - `slug: Mapped[str]`（unique）
  - `enabled: Mapped[bool]`（默认 True）
  - `metric_profile: Mapped[str]`（默认 `"java"`）
  - `datasource_config: Mapped[dict]`（JSONB，默认 `{}`；加密细节留 M1）
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
  4. `run_migrations()`：代码内 `alembic.command.upgrade(config, "head")`，`config.set_main_option("script_location", <abs path>)` 修正路径
  5. `ping_db` / `ping_redis`（失败记 WARN，不阻断启动）
  6. `yield`
  7. `await engine.dispose()` + `await redis.aclose()`
- 中间件（注意 LIFO，后加先执行）：RequestId（注入 `trace_id_var`）→ CORS（`settings.cors_origins`）→ 日志。
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
```

---

## 7. 测试策略

pytest + pytest-asyncio + httpx.AsyncClient。需 DB/Redis（docker-compose 起的实例；测试 env 指向本地）。

| 测试文件 | 覆盖 |
|---|---|
| `test_config.py` | Settings 正常加载；缺 `JWT_SECRET` 抛错 |
| `test_security.py` | HS256 验签通过；过期 token → 4010；非 admin → 4030；缺 token → 4010 |
| `test_project_context.py` | 缺 `X-Project-Id` → 4001；非 UUID → 4001；合法 UUID 通过 |
| `test_response.py` | `success()` 包装；`APIError` → `{code,message,detail}`；`Page` 结构 |
| `test_health.py` | `/health` 真实连通 DB/Redis；`/metrics` 占位可访问 |
| `test_whoami.py` | 端到端：合法 admin + 合法 project → 200 `{code:0}`；缺任一 → 对应错误码 |
| `conftest.py` | async fixtures：测试 engine（建表/清理）、`AsyncClient`、`make_jwt(role, exp)` 工具 |

完成前跑 `uv run pytest`、`uv run ruff check .`、`uv run mypy app`。

---

## 8. 边界与非目标（M0 不做）

- 不实现 projects CRUD / 数据源连通性校验（M1）
- 不实现 project_context 的库存在性校验（M1）
- 不实现 Provider / Profile / 采集器 / Agent / 告警 / 自愈 / WebSocket（M2+）
- `/metrics` 不含真实业务指标（M6）
- 不实现非 admin 角色（全程仅 admin）
