# M0 地基层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 mo-chat-aiops 后端地基层：core/ 全套基础设施 + FastAPI 骨架 + 统一响应 + Alembic 启动自动迁移 + 本地 docker 依赖 + 测试骨架。

**Architecture:** 全异步 FastAPI（SQLAlchemy 2.0 async + asyncpg + redis.asyncio）。`get_app()` 工厂建 app，async engine/redis 在 lifespan 内创建并注入 `app.state`，启动时代码内跑 `alembic upgrade head`。鉴权用 PyJWT HS256（仅 admin），项目隔离经 `X-Project-Id` 依赖（M0 仅格式校验）。统一响应 `{code:0,data}`，错误经 `APIError` + exception_handler 包装。

**Tech Stack:** Python 3.13, uv, FastAPI, SQLAlchemy 2.0 async, asyncpg, Alembic, redis.asyncio, PyJWT, pydantic-settings, pytest + pytest-asyncio + httpx.

---

## 文件结构总览

| 文件 | 职责 |
|---|---|
| `app/core/config.py` | pydantic-settings 单一 Settings |
| `app/core/constants.py` | ErrorCode/SERVICE_NAME/redis_key 单点声明 |
| `app/core/logging.py` | dictConfig + JSON formatter + contextvars |
| `app/core/db.py` | async engine/sessionmaker + get_session + ping_db |
| `app/core/redis.py` | redis 连接池 + get_redis + ping_redis |
| `app/core/security.py` | PyJWT HS256 鉴权依赖 + CurrentUser |
| `app/core/project_context.py` | X-Project-Id 依赖（格式校验） |
| `app/schemas/response.py` | APIError + success() + Page |
| `app/models/base.py` | DeclarativeBase |
| `app/models/project.py` | Project ORM |
| `app/repositories/base.py` | ProjectScopedRepository 隔离基类（防漏写 project_id） |
| `app/api/health.py` | /health + /metrics + /api/whoami |
| `app/main.py` | get_app 工厂 + lifespan + 异常处理 |
| `app/db/migrate.py` | 代码内 alembic upgrade head 入口 |
| `app/db/migrations/env.py` | Alembic async env |
| `app/db/migrations/versions/0001_create_projects.py` | 建 projects 表 |
| `docker/docker-compose.yml` | postgres + redis |
| `tests/conftest.py` | async fixtures + DB/缓存隔离 |

依赖顺序：Task 1（脚手架）→ 2（config）→ 3（constants）→ 4（response）→ 5（logging）→ 6（db）→ 7（redis）→ 8（security）→ 9（project_context）→ 10（models）→ 10b（repositories 基类）→ 11（docker）→ 12（alembic）→ 13（health/whoami）→ 14（main 装配 + conftest）→ 15（lint/最终验证）。

---

### Task 1: 项目脚手架与依赖

**Files:**
- Create: `app/__init__.py`, `app/core/__init__.py`, `app/schemas/__init__.py`, `app/models/__init__.py`, `app/api/__init__.py`, `app/db/__init__.py`, `tests/__init__.py`
- Create: `.env.example`
- Modify: `pyproject.toml`（加 `[tool.pytest.ini_options]`）

- [ ] **Step 1: 添加运行时依赖**

```bash
uv add "python-json-logger>=2.0,<3"
```

（pin `<3`：3.x 把 `jsonlogger.JsonFormatter` 迁到了 `pythonjsonlogger.json`，pin 住避免导入路径破坏。）

（其余依赖 fastapi/sqlalchemy/asyncpg/alembic/redis/pyjwt/pydantic-settings/uvicorn 已在 pyproject.toml；dev 组 pytest/pytest-asyncio/httpx/ruff/mypy 已在。）

- [ ] **Step 2: 创建包占位文件**

```bash
mkdir -p app/core app/schemas app/models app/api app/db tests
touch app/__init__.py app/core/__init__.py app/schemas/__init__.py app/models/__init__.py app/api/__init__.py app/db/__init__.py tests/__init__.py
```

- [ ] **Step 3: 配置 pytest（asyncio 自动模式）**

在 `pyproject.toml` 末尾追加：

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py313"
line-length = 100

[tool.mypy]
python_version = "3.13"
ignore_missing_imports = true
```

- [ ] **Step 4: 创建 `.env.example`**

```
DATABASE_URL=postgresql+asyncpg://aiops:aiops@localhost:5432/mo_aiops
REDIS_URL=redis://localhost:6379
REDIS_DB=1
JWT_SECRET=change-me-shared-with-java
LOG_FORMAT=dev
ENVIRONMENT=dev
# 逗号分隔；Settings 的 field_validator 会拆成 list
CORS_ORIGINS=*
```

- [ ] **Step 5: 验证依赖同步**

Run: `uv sync`
Expected: 成功，无错误。

- [ ] **Step 6: Commit**

```bash
git add -f app tests pyproject.toml uv.lock .env.example
git commit -m "chore: scaffold app package, deps, pytest config"
```

---

### Task 2: core/config.py — Settings

**Files:**
- Create: `app/core/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:

```python
import pytest
from app.core.config import Settings, get_settings


def test_settings_loads_required_fields(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("JWT_SECRET", "secret")
    get_settings.cache_clear()
    s = get_settings()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.redis_db == 1
    assert s.jwt_algorithm == "HS256"


def test_settings_missing_jwt_secret_raises(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(Exception):
        Settings(_env_file=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL（`ModuleNotFoundError: app.core.config`）

- [ ] **Step 3: Write implementation**

`app/core/config.py`:

```python
"""启动期静态配置（pydantic-settings 单一来源）。"""
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """从 .env / 环境变量加载的启动配置。敏感字段禁止进日志。"""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 数据库与缓存
    database_url: str
    redis_url: str
    redis_db: int = 1

    # 鉴权（与 Java 主服务共享对称 Secret）
    jwt_secret: str
    jwt_algorithm: str = "HS256"

    # 服务间调用（M0 留位）
    java_service_internal_token: str | None = None

    # 数据源凭证加密密钥（M0 留位，M1 接入 cryptography 解密时启用）
    datasource_secret_key: str | None = None

    # LLM 接入（M0 留位，全局非 per-project）
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None

    # 横切
    cors_origins: Annotated[list[str], NoDecode] = ["*"]
    log_format: Literal["dev", "json"] = "json"
    environment: Literal["dev", "prod"] = "dev"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: object) -> object:
        """允许 env 用逗号分隔字符串（如 CORS_ORIGINS=*,http://x）。
        NoDecode 关掉 pydantic-settings 对复杂类型的 JSON 预解码，
        否则 `*` 会在 source 层就报错（validator 根本来不及跑）。"""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    """进程内单例配置。"""
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -f app/core/config.py tests/test_config.py
git commit -m "feat(core): add Settings via pydantic-settings"
```

---

### Task 3: core/constants.py — 常量单点声明

**Files:**
- Create: `app/core/constants.py`
- Test: `tests/test_constants.py`

- [ ] **Step 1: Write the failing test**

`tests/test_constants.py`:

```python
from app.core.constants import ErrorCode, RedisKey, SERVICE_NAME


def test_error_codes():
    assert ErrorCode.SUCCESS == 0
    assert ErrorCode.INVALID_PROJECT == 4001
    assert ErrorCode.UNAUTHORIZED == 4010
    assert ErrorCode.FORBIDDEN == 4030


def test_service_name():
    assert SERVICE_NAME == "mo-chat-aiops"


def test_redis_key_suffix_single_source():
    # 后缀是单点常量，不在调用点写字面量
    assert RedisKey.AGENT_STATUS == "agent:status"
    assert RedisKey.of("p1", RedisKey.AGENT_STATUS) == "aiops:p1:agent:status"
    assert RedisKey.of("p1", RedisKey.INGEST) == "aiops:p1:ingest"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_constants.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Write implementation**

`app/core/constants.py`:

```python
"""全局常量单点声明：错误码、服务名、Redis key（后缀 + 命名空间拼接）。

错误码分段约定（后续里程碑按域扩展，避免拍脑袋编号）：
  40xx = 鉴权 / 项目上下文     41xx = 告警域
  42xx = 自愈域 / 请求校验      43xx = Agent 域
  5xxx = 系统内部错误
"""
from enum import IntEnum

SERVICE_NAME = "mo-chat-aiops"


class ErrorCode(IntEnum):
    """统一业务错误码（body 内 code 字段）。"""

    SUCCESS = 0
    # 40xx 鉴权 / 上下文
    INVALID_PROJECT = 4001
    UNAUTHORIZED = 4010
    FORBIDDEN = 4030
    # 42xx 请求校验
    VALIDATION_ERROR = 4220
    # 5xxx 系统
    INTERNAL = 5000


class RedisKey:
    """Redis key 后缀单点声明 + 项目命名空间拼接，禁止散落字面量。

    后续里程碑用到的 key 后缀在此登记，调用点引用常量，不写字符串。
    """

    AGENT_STATUS = "agent:status"     # M3
    INGEST = "ingest"                 # M4 入站处理队列
    ALERTS = "alerts"                 # M4 出站前端推送通道
    TOPOLOGY_CACHE = "topology:cache" # M5
    CONFIG = "config"                 # 运行时热更新配置 Hash
    WS_AGENT = "ws:agent"             # M5 PubSub
    WS_HEAL = "ws:heal"               # M5 PubSub
    RECENT_ERRORS = "recent_errors"   # M2 日志采集

    @staticmethod
    def of(project_id: str, suffix: str) -> str:
        """拼接项目命名空间 key：aiops:{project_id}:{suffix}。"""
        return f"aiops:{project_id}:{suffix}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_constants.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -f app/core/constants.py tests/test_constants.py
git commit -m "feat(core): add constants (ErrorCode segments, RedisKey single-source)"
```

---

### Task 4: schemas/response.py — 统一响应

**Files:**
- Create: `app/schemas/response.py`
- Test: `tests/test_response.py`

- [ ] **Step 1: Write the failing test**

`tests/test_response.py`:

```python
from app.core.constants import ErrorCode
from app.schemas.response import APIError, ApiResponse, ErrorResponse, Page, success


def test_success_wrapper():
    assert success({"a": 1}) == {"code": 0, "data": {"a": 1}}


def test_api_response_model():
    resp = ApiResponse[dict](data={"a": 1})
    assert resp.code == 0
    assert resp.model_dump() == {"code": 0, "data": {"a": 1}}


def test_error_response_model():
    err = ErrorResponse(code=4001, message="bad", detail=None)
    assert err.model_dump() == {"code": 4001, "message": "bad", "detail": None}


def test_api_error_fields():
    err = APIError(code=ErrorCode.INVALID_PROJECT, message="bad", detail={"x": 1})
    assert err.code == 4001
    assert err.message == "bad"
    assert err.detail == {"x": 1}
    assert err.http_status == 200


def test_page_model():
    p = Page(total=10, page=1, size=5, items=[1, 2, 3])
    assert p.model_dump() == {"total": 10, "page": 1, "size": 5, "items": [1, 2, 3]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_response.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Write implementation**

`app/schemas/response.py`:

```python
"""统一响应包装与业务异常。

- ApiResponse[T] / ErrorResponse：类型化信封，供路由声明 response_model，
  让 FastAPI 自动生成 OpenAPI schema（契约即文档，AGENTS.md §9）。
- success()：便捷构造成功 body。
- APIError：业务异常，HTTP 200 + body 内携带业务 code。
"""
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """成功响应信封：{code:0, data}。路由用 response_model=ApiResponse[XxxData]。"""

    code: int = 0
    data: T


class ErrorResponse(BaseModel):
    """失败响应信封：{code, message, detail}。"""

    code: int
    message: str
    detail: Any = None


class APIError(Exception):
    """业务异常：HTTP 200 + body 内携带业务 code。"""

    def __init__(
        self, code: int, message: str, detail: Any = None, http_status: int = 200
    ) -> None:
        self.code = int(code)
        self.message = message
        self.detail = detail
        self.http_status = http_status
        super().__init__(message)


def success(data: Any) -> dict:
    """成功响应包装（便捷构造；需 OpenAPI schema 时用 ApiResponse[T]）。"""
    return {"code": 0, "data": data}


class Page(BaseModel, Generic[T]):
    """分页响应模型。"""

    total: int
    page: int
    size: int
    items: list[T]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_response.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -f app/schemas/response.py tests/test_response.py
git commit -m "feat(schemas): add unified response (APIError, success, Page)"
```

---

### Task 5: core/logging.py — 结构化日志

**Files:**
- Create: `app/core/logging.py`
- Test: `tests/test_logging.py`

- [ ] **Step 1: Write the failing test**

`tests/test_logging.py`:

```python
import json
import logging

from app.core.logging import set_trace_id, setup_logging


def test_setup_logging_json(capsys):
    setup_logging(log_format="json")
    set_trace_id("trace-123")
    logging.getLogger("test").info("hello")
    captured = capsys.readouterr()
    out = captured.out + captured.err
    # 至少一条 JSON 行包含 service 与 trace_id
    lines = [l for l in out.splitlines() if l.strip().startswith("{")]
    assert lines, "no json log emitted"
    rec = json.loads(lines[-1])
    assert rec["service"] == "mo-chat-aiops"
    assert rec.get("trace_id") == "trace-123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_logging.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Write implementation**

`app/core/logging.py`:

```python
"""结构化 JSON 日志：dictConfig + contextvars 注入 trace_id/project_id。"""
import logging
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

from app.core.constants import SERVICE_NAME

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
project_id_var: ContextVar[str] = ContextVar("project_id", default="")


def set_trace_id(value: str) -> None:
    """设置当前请求 trace_id（中间件调用）。"""
    trace_id_var.set(value)


def set_project_id(value: str) -> None:
    """设置当前请求 project_id。"""
    project_id_var.set(value)


class ContextFilter(logging.Filter):
    """把 contextvars 注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = SERVICE_NAME
        record.trace_id = trace_id_var.get()
        record.project_id = project_id_var.get()
        return True


def setup_logging(log_format: str = "json") -> None:
    """配置根 logger；dev 人类可读，json 结构化。"""
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    if log_format == "json":
        fmt = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s "
            "%(service)s %(trace_id)s %(project_id)s"
        )
    else:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s "
            "(trace=%(trace_id)s project=%(project_id)s): %(message)s"
        )
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_logging.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -f app/core/logging.py tests/test_logging.py
git commit -m "feat(core): add structured JSON logging with contextvars"
```

---

### Task 6: core/db.py — async 引擎/会话

**Files:**
- Create: `app/core/db.py`
- Test: `tests/test_db.py`

> 本任务测试需真实 PostgreSQL（docker-compose 在 Task 11 提供；若先做此任务，需本地 PG 可用）。测试从 env 读 `DATABASE_URL`。

- [ ] **Step 1: Write the failing test**

`tests/test_db.py`:

```python
import os

import pytest

from app.core.db import create_engine, make_sessionmaker, ping_db


@pytest.fixture
def engine():
    url = os.environ["DATABASE_URL"]
    eng = create_engine(url)
    yield eng


async def test_ping_db_ok(engine):
    assert await ping_db(engine) is True


async def test_sessionmaker_yields_session(engine):
    sm = make_sessionmaker(engine)
    async with sm() as session:
        from sqlalchemy import text

        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Write implementation**

`app/core/db.py`:

```python
"""SQLAlchemy 2.0 async 引擎与会话。engine 在 lifespan 内创建，不放模块全局。"""
import json
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _json_serializer(obj: object) -> str:
    """JSONB 序列化：default=str 解决 datetime/UUID。"""
    return json.dumps(obj, default=str)


def create_engine(database_url: str) -> AsyncEngine:
    """创建 async 引擎（连接池 min5/max20 概念：pool_size5 + overflow15）。"""
    return create_async_engine(
        database_url,
        pool_size=5,
        max_overflow=15,
        pool_pre_ping=True,
        json_serializer=_json_serializer,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建 session 工厂。"""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：从 app.state.sessionmaker 取一个 session。"""
    sm: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sm() as session:
        yield session


async def ping_db(engine: AsyncEngine) -> bool:
    """探活：SELECT 1，异常返回 False。"""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS（需 PG 可用）

- [ ] **Step 5: Commit**

```bash
git add -f app/core/db.py tests/test_db.py
git commit -m "feat(core): add async db engine, sessionmaker, ping_db"
```

---

### Task 7: core/redis.py — async Redis

**Files:**
- Create: `app/core/redis.py`
- Test: `tests/test_redis.py`

> 需真实 Redis（docker-compose 在 Task 11）。

- [ ] **Step 1: Write the failing test**

`tests/test_redis.py`:

```python
import os

import pytest

from app.core.redis import create_redis, ping_redis


@pytest.fixture
async def redis_client():
    client = create_redis(os.environ["REDIS_URL"], db=1)
    yield client
    await client.aclose()


async def test_ping_redis_ok(redis_client):
    assert await ping_redis(redis_client) is True


async def test_set_get(redis_client):
    await redis_client.set("aiops:test:k", "v")
    assert await redis_client.get("aiops:test:k") == "v"
    await redis_client.delete("aiops:test:k")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_redis.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Write implementation**

`app/core/redis.py`:

```python
"""redis.asyncio 客户端（固定 DB 1）。"""
from fastapi import Request
from redis.asyncio import Redis, from_url


def create_redis(redis_url: str, db: int = 1) -> Redis:
    """创建 async redis 客户端，decode_responses=True。"""
    return from_url(redis_url, db=db, decode_responses=True)


async def get_redis(request: Request) -> Redis:
    """FastAPI 依赖：取 app.state.redis。"""
    return request.app.state.redis


async def ping_redis(client: Redis) -> bool:
    """探活：PING，异常返回 False。"""
    try:
        return bool(await client.ping())
    except Exception:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_redis.py -v`
Expected: PASS（需 Redis 可用）

- [ ] **Step 5: Commit**

```bash
git add -f app/core/redis.py tests/test_redis.py
git commit -m "feat(core): add async redis client and ping_redis"
```

---

### Task 8: core/security.py — JWT 鉴权

**Files:**
- Create: `app/core/security.py`
- Test: `tests/test_security.py`

- [ ] **Step 1: Write the failing test**

`tests/test_security.py`:

```python
import time

import jwt
import pytest

from app.core.security import CurrentUser, decode_and_verify
from app.schemas.response import APIError

SECRET = "secret"


def _token(role="admin", exp_delta=3600, sub="u1"):
    payload = {"sub": sub, "role": role, "exp": int(time.time()) + exp_delta}
    return jwt.encode(payload, SECRET, algorithm="HS256")


def test_valid_admin_token():
    user = decode_and_verify(_token(), SECRET, "HS256")
    assert isinstance(user, CurrentUser)
    assert user.role == "admin"
    assert user.user_id == "u1"


def test_expired_token():
    with pytest.raises(APIError) as e:
        decode_and_verify(_token(exp_delta=-10), SECRET, "HS256")
    assert e.value.code == 4010


def test_non_admin_forbidden():
    with pytest.raises(APIError) as e:
        decode_and_verify(_token(role="user"), SECRET, "HS256")
    assert e.value.code == 4030


def test_garbage_token():
    with pytest.raises(APIError) as e:
        decode_and_verify("not-a-token", SECRET, "HS256")
    assert e.value.code == 4010
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_security.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Write implementation**

`app/core/security.py`:

```python
"""PyJWT HS256 鉴权依赖（MVP 仅 admin）。"""
import jwt
from fastapi import Header
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.constants import ErrorCode
from app.schemas.response import APIError


class CurrentUser(BaseModel):
    """已鉴权用户身份。"""

    user_id: str | None
    role: str


def decode_and_verify(token: str, secret: str, algorithm: str) -> CurrentUser:
    """解码 JWT，校验签名/过期/角色。失败抛 APIError。"""
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError:
        raise APIError(ErrorCode.UNAUTHORIZED, "token expired")
    except jwt.InvalidTokenError:
        raise APIError(ErrorCode.UNAUTHORIZED, "invalid token")
    if payload.get("role") != "admin":
        raise APIError(ErrorCode.FORBIDDEN, "admin role required")
    return CurrentUser(user_id=payload.get("sub"), role="admin")


async def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """FastAPI 依赖：从 Authorization: Bearer 取 token 并校验。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise APIError(ErrorCode.UNAUTHORIZED, "missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    settings = get_settings()
    return decode_and_verify(token, settings.jwt_secret, settings.jwt_algorithm)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_security.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -f app/core/security.py tests/test_security.py
git commit -m "feat(core): add JWT HS256 auth dependency"
```

---

### Task 9: core/project_context.py — X-Project-Id 依赖

**Files:**
- Create: `app/core/project_context.py`
- Test: `tests/test_project_context.py`

- [ ] **Step 1: Write the failing test**

`tests/test_project_context.py`:

```python
import uuid

import pytest

from app.core.project_context import resolve_project_id
from app.schemas.response import APIError


def test_valid_uuid():
    pid = str(uuid.uuid4())
    assert resolve_project_id(pid) == pid


def test_missing_returns_4001():
    with pytest.raises(APIError) as e:
        resolve_project_id(None)
    assert e.value.code == 4001


def test_invalid_format_returns_4001():
    with pytest.raises(APIError) as e:
        resolve_project_id("not-a-uuid")
    assert e.value.code == 4001
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_project_context.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Write implementation**

`app/core/project_context.py`:

```python
"""X-Project-Id 项目上下文依赖（M0 仅格式校验，库存在性校验留 M1）。"""
import uuid

from fastapi import Header

from app.core.constants import ErrorCode
from app.core.logging import set_project_id
from app.schemas.response import APIError


def resolve_project_id(raw: str | None) -> str:
    """校验 X-Project-Id 存在且为合法 UUID，否则抛 4001。"""
    if not raw:
        raise APIError(ErrorCode.INVALID_PROJECT, "missing or invalid project")
    try:
        uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        raise APIError(ErrorCode.INVALID_PROJECT, "missing or invalid project")
    return raw


async def get_project_id(
    x_project_id: str | None = Header(default=None),
) -> str:
    """FastAPI 依赖：解析并校验 X-Project-Id，注入日志 contextvar。"""
    pid = resolve_project_id(x_project_id)
    set_project_id(pid)
    return pid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_project_context.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -f app/core/project_context.py tests/test_project_context.py
git commit -m "feat(core): add X-Project-Id context dependency"
```

---

### Task 10: models — Base + Project ORM

**Files:**
- Create: `app/models/base.py`
- Create: `app/models/project.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:

```python
from app.models.base import Base
from app.models.project import Project


def test_project_table_registered():
    assert "projects" in Base.metadata.tables


def test_project_columns():
    cols = Base.metadata.tables["projects"].columns.keys()
    for c in ["id", "name", "slug", "enabled", "metric_profile",
              "datasource_config", "created_at", "updated_at"]:
        assert c in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: Write implementation**

`app/models/base.py`:

```python
"""SQLAlchemy 2.0 声明式基类（Alembic metadata 源）。"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    pass
```

`app/models/project.py`:

```python
"""projects 表：多项目核心，唯一不带 project_id 的业务表。"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Project(Base):
    """被监控项目元数据 + 数据源连接配置。"""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metric_profile: Mapped[str] = mapped_column(
        String(32), nullable=False, default="java"
    )
    datasource_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -f app/models/base.py app/models/project.py tests/test_models.py
git commit -m "feat(models): add Base and Project ORM"
```

---

### Task 10b: repositories — 项目隔离基类（防漏写 project_id）

**Files:**
- Create: `app/repositories/__init__.py`
- Create: `app/repositories/base.py`
- Test: `tests/test_repositories.py`

> 兑现 spec §2「M0 铺好 project_id 隔离基础设施」的承诺。M0 不接真实业务仓库，只立基类：构造期绑定 `session + project_id`，查询经 `scope()` 强制注入过滤，让「漏写 project_id」在构造期就不可能。M1+ 所有业务仓库继承它。`projects` 表本身无 project_id，是唯一例外，不继承此基类。

- [ ] **Step 1: Write the failing test**

`tests/test_repositories.py`:

```python
import uuid

from sqlalchemy import Column, String, select
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base
from app.repositories.base import ProjectScopedRepository


class _Dummy(Base):
    """仅测试用的带 project_id 的表。"""

    __tablename__ = "dummy_scoped"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String(64), nullable=False)
    name = Column(String(64))


class _DummyRepo(ProjectScopedRepository):
    project_column = _Dummy.project_id


def test_scope_injects_project_filter():
    repo = _DummyRepo(session=None, project_id="p-123")  # session 不参与本断言
    stmt = repo.scope(select(_Dummy))
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    # 注入了 WHERE ... project_id = 'p-123'
    assert "project_id" in compiled
    assert "p-123" in compiled


def test_repo_binds_project_id():
    repo = _DummyRepo(session=None, project_id="p-xyz")
    assert repo.project_id == "p-xyz"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repositories.py -v`
Expected: FAIL（`ModuleNotFoundError: app.repositories.base`）

- [ ] **Step 3: Write implementation**

`app/repositories/__init__.py`: 空文件。

`app/repositories/base.py`:

```python
"""业务仓库基类：构造即绑定 project_id，查询强制注入过滤，杜绝漏写。

M1+ 所有带 project_id 的业务表仓库继承本类，通过 self.scope(stmt) 查询。
projects 表本身无 project_id，是唯一例外，不继承此基类。
"""
from typing import Any

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement


class ProjectScopedRepository:
    """业务表仓库基类。session + project_id 在构造期绑定。"""

    #: 子类指定本表的 project_id 列（如 AlertEvent.project_id）
    project_column: ColumnElement[Any]

    def __init__(self, session: AsyncSession | None, project_id: str) -> None:
        self.session = session
        self.project_id = project_id

    def scope(self, stmt: Select) -> Select:
        """给任意 SELECT 注入 WHERE project_id = self.project_id。"""
        # 经类访问绕过 InstrumentedAttribute 描述符（实例访问会触发 __get__
        # 而 repo 实例非 mapped 对象，会抛 AttributeError）。
        col = type(self).project_column
        return stmt.where(col == self.project_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_repositories.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -f app/repositories tests/test_repositories.py
git commit -m "feat(repositories): add ProjectScopedRepository base for project_id isolation"
```

---

### Task 11: docker — 本地 PostgreSQL + Redis

**Files:**
- Create: `docker/docker-compose.yml`
- Create: `docker/.env.example`

- [ ] **Step 1: 创建 compose 文件**

`docker/docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: mo_aiops
      POSTGRES_USER: aiops
      POSTGRES_PASSWORD: aiops
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U aiops -d mo_aiops"]
      interval: 5s
      timeout: 3s
      retries: 10
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  pgdata: {}
```

`docker/.env.example`:

```
DATABASE_URL=postgresql+asyncpg://aiops:aiops@localhost:5432/mo_aiops
REDIS_URL=redis://localhost:6379
REDIS_DB=1
JWT_SECRET=change-me-shared-with-java
LOG_FORMAT=dev
ENVIRONMENT=dev
```

- [ ] **Step 2: 启动并验证健康**

Run: `docker compose -f docker/docker-compose.yml up -d`
Run: `docker compose -f docker/docker-compose.yml ps`
Expected: postgres 与 redis 均 `healthy`。

- [ ] **Step 3: 准备本地 .env**

```bash
cp docker/.env.example .env
```

Expected: `.env` 存在（已被 gitignore，不提交）。

- [ ] **Step 4: Commit**

```bash
git add -f docker/docker-compose.yml docker/.env.example
git commit -m "chore(docker): add local postgres + redis compose"
```

---

### Task 12: Alembic — async env + 首个迁移

**Files:**
- Create: `alembic.ini`
- Create: `app/db/migrations/env.py`
- Create: `app/db/migrations/script.py.mako`
- Create: `app/db/migrations/versions/0001_create_projects.py`
- Create: `app/db/migrate.py`（代码内 upgrade 入口）
- Test: `tests/test_migrations.py`

> 需 PG 可用（Task 11）。

- [ ] **Step 1: 创建 alembic.ini**

`alembic.ini`:

```ini
[alembic]
script_location = app/db/migrations
file_template = %%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s
prepend_sys_path = .

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 2: 创建 script 模板**

`app/db/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 3: 创建 async env.py**

`app/db/migrations/env.py`:

```python
"""Alembic async 环境：从 Settings 读 URL，import 所有 model 进 metadata。"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models.base import Base

# 关键：import 所有 model 模块，确保表注册进 metadata（autogenerate 可见）
import app.models.project  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection) -> None:
    """在已建立的连接上运行迁移。"""
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """async 引擎跑迁移（run_sync 桥接同步迁移上下文）。"""
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    """离线模式（生成 SQL）。"""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
```

- [ ] **Step 4: 创建首个迁移**

`app/db/migrations/versions/0001_create_projects.py`:

```python
"""create projects table

Revision ID: 0001
Revises:
Create Date: 2026-06-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metric_profile", sa.String(32), nullable=False,
                  server_default="java"),
        sa.Column("datasource_config", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_unique_constraint("uq_projects_slug", "projects", ["slug"])


def downgrade() -> None:
    op.drop_constraint("uq_projects_slug", "projects", type_="unique")
    op.drop_table("projects")
```

- [ ] **Step 5: 创建代码内迁移入口**

`app/db/migrate.py`:

```python
"""代码内执行 Alembic upgrade head（启动时调用）。"""
from pathlib import Path

from alembic import command
from alembic.config import Config

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_upgrade_head() -> None:
    """绝对路径修正 script_location 后 upgrade 到最新。

    注意：env.py 在线模式用 asyncio.run() 建临时 loop 跑迁移。
    因此本函数必须在「没有运行中的事件循环」的线程里调用——
    在 FastAPI lifespan / async 测试中要用 `await asyncio.to_thread(run_upgrade_head)`，
    否则会触发 RuntimeError: asyncio.run() cannot be called from a running event loop。
    """
    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(_PROJECT_ROOT / "app" / "db" / "migrations")
    )
    command.upgrade(cfg, "head")
```

- [ ] **Step 6: Write the test**

`tests/test_migrations.py`:

```python
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import run_upgrade_head


async def test_upgrade_creates_projects_table():
    # run_upgrade_head 内部走 asyncio.run，必须丢到无 loop 的线程执行
    await asyncio.to_thread(run_upgrade_head)
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT to_regclass('public.projects')"))
        assert result.scalar() is not None
    await engine.dispose()
```

> 用 asyncpg（项目已装）做异步断言，不引入同步驱动（项目无 psycopg）。`to_thread` 保证 `run_upgrade_head` 不在运行中的事件循环里调 `asyncio.run`。

- [ ] **Step 7: Run migration manually + test**

Run: `uv run alembic upgrade head`
Expected: 无错误，projects 表创建。
Run: `uv run pytest tests/test_migrations.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add -f alembic.ini app/db/migrations app/db/migrate.py tests/test_migrations.py
git commit -m "feat(db): add async alembic env + projects migration + auto-upgrade"
```

---

### Task 13: api/health.py — health/metrics/whoami

**Files:**
- Create: `app/api/health.py`
- Test: `tests/test_health.py`、`tests/test_whoami.py`（依赖 Task 14 的 app，先写桩，Task 14 装配后跑通）

> 本任务路由依赖 `app` 实例与 `conftest` 的 AsyncClient（Task 14 提供 `conftest.py`）。建议与 Task 14 连续执行；测试在 Task 14 后运行。

- [ ] **Step 1: Write health router**

`app/api/health.py`:

```python
"""健康检查 / metrics 占位 / whoami 探针。"""
import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from app.core.db import ping_db
from app.core.project_context import get_project_id
from app.core.redis import ping_redis
from app.core.security import CurrentUser, get_current_user
from app.schemas.response import success

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    """探活 DB + Redis（HTTP 200，body 标明 healthy/degraded）。"""
    db_ok, redis_ok = await asyncio.gather(
        ping_db(request.app.state.engine),
        ping_redis(request.app.state.redis),
    )
    status = "healthy" if (db_ok and redis_ok) else "degraded"
    return success(
        {
            "status": status,
            "db": "ok" if db_ok else "down",
            "redis": "ok" if redis_ok else "down",
        }
    )


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    """Prometheus 抓取端点（M0 占位，M6 接入真实指标）。"""
    return "# mo-chat-aiops metrics placeholder\n"


@router.get("/api/whoami")
async def whoami(
    user: CurrentUser = Depends(get_current_user),
    project_id: str = Depends(get_project_id),
) -> dict:
    """端到端探针：验证鉴权 + 项目隔离 + 统一响应。"""
    return success(
        {"user_id": user.user_id, "role": user.role, "project_id": project_id}
    )
```

- [ ] **Step 2: Write tests（Task 14 后运行）**

`tests/test_health.py`:

```python
async def test_health_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "healthy"
    assert body["data"]["db"] == "ok"
    assert body["data"]["redis"] == "ok"


async def test_metrics_accessible(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "placeholder" in resp.text
```

`tests/test_whoami.py`:

```python
import uuid


async def test_whoami_ok(client, make_jwt):
    headers = {
        "Authorization": f"Bearer {make_jwt(role='admin')}",
        "X-Project-Id": str(uuid.uuid4()),
    }
    resp = await client.get("/api/whoami", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["role"] == "admin"


async def test_whoami_missing_token(client):
    resp = await client.get("/api/whoami", headers={"X-Project-Id": str(uuid.uuid4())})
    assert resp.json()["code"] == 4010


async def test_whoami_missing_project(client, make_jwt):
    headers = {"Authorization": f"Bearer {make_jwt(role='admin')}"}
    resp = await client.get("/api/whoami", headers=headers)
    assert resp.json()["code"] == 4001


async def test_whoami_non_admin(client, make_jwt):
    headers = {
        "Authorization": f"Bearer {make_jwt(role='user')}",
        "X-Project-Id": str(uuid.uuid4()),
    }
    resp = await client.get("/api/whoami", headers=headers)
    assert resp.json()["code"] == 4030
```

> `RequestValidationError → {code:4220}` 处理器的*形态*已由 `test_response.py::test_error_response_model` + main.py 注册覆盖。M0 无带强校验 query/body 的业务端点，无法构造真实 422；待 M1 出现此类接口时，在该接口测试里补一条真实触发 422 的用例（不在此写空断言制造假绿）。

- [ ] **Step 3: Commit（实现部分；测试随 Task 14 跑通后再确认）**

```bash
git add -f app/api/health.py tests/test_health.py tests/test_whoami.py
git commit -m "feat(api): add health, metrics placeholder, whoami probe"
```

---

### Task 14: main.py — app 装配 + lifespan + 异常处理 + conftest

**Files:**
- Create: `app/main.py`
- Create: `tests/conftest.py`
- Run: 全量测试

- [ ] **Step 1: Write main.py**

`app/main.py`:

```python
"""FastAPI app 工厂：lifespan 建 engine/redis + 自动迁移 + 异常处理。"""
import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.db import create_engine, make_sessionmaker, ping_db
from app.core.logging import set_trace_id, setup_logging
from app.core.redis import create_redis, ping_redis
from app.core.constants import ErrorCode
from app.db.migrate import run_upgrade_head
from app.schemas.response import APIError


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：日志→engine→redis→迁移→探活；关闭：dispose。"""
    settings = get_settings()
    setup_logging(settings.log_format)

    app.state.engine = create_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.redis = create_redis(settings.redis_url, db=settings.redis_db)

    # 迁移走线程：env.py 在线模式用 asyncio.run() 建临时 loop，
    # 在已运行的 lifespan 事件循环里直接调会抛 RuntimeError。
    await asyncio.to_thread(run_upgrade_head)

    import logging

    log = logging.getLogger("startup")
    if not await ping_db(app.state.engine):
        log.warning("database ping failed at startup")
    if not await ping_redis(app.state.redis):
        log.warning("redis ping failed at startup")

    yield

    await app.state.engine.dispose()
    await app.state.redis.aclose()


def get_app() -> FastAPI:
    """app 工厂。"""
    settings = get_settings()
    app = FastAPI(title="mo-chat-aiops", lifespan=lifespan)

    # 中间件（LIFO：后加先执行）。CORS 先加，trace 后加 → trace 最先跑注入 id。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        """注入 trace_id 到 contextvar 与响应头。"""
        trace_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        set_trace_id(trace_id)
        response = await call_next(request)
        response.headers["X-Request-Id"] = trace_id
        return response

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        """业务异常 → 统一 body（HTTP 200，code 在 body）。"""
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """请求校验失败 → 统一 body。"""
        return JSONResponse(
            status_code=200,
            content={
                "code": ErrorCode.VALIDATION_ERROR,
                "message": "validation error",
                "detail": exc.errors(),
            },
        )

    app.include_router(health_router)
    return app


app = get_app()
```

- [ ] **Step 2: Write conftest.py**

`tests/conftest.py`:

```python
"""async 测试 fixtures：app、AsyncClient、签 JWT、DB/缓存隔离。"""
import time

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """每个测试前后清 lru_cache，防 monkeypatch env 跨测试污染。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
async def app_instance():
    """会话级 app：lifespan 只跑一次（建 engine/redis + 迁移），避免每测重建。

    仅被需要 DB/Redis 的集成测试（经 client fixture）触发，
    纯单元测试（config/constants/response/security/project_context/logging/repositories）
    不依赖它，故不需要 DB 在场。
    """
    from app.main import get_app

    application = get_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def _clean_db_and_redis(app_instance):
    """测试后清业务表与测试 Redis key，保证集成测试间状态隔离。

    非 autouse：由 client fixture 依赖触发，只作用于真正打 DB 的测试。
    M0 仅 projects 一张业务表；M1+ 新增业务表时在此追加 TRUNCATE。
    """
    yield
    engine = app_instance.state.engine
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE projects RESTART IDENTITY CASCADE"))
    redis = app_instance.state.redis
    keys = await redis.keys("aiops:*")
    if keys:
        await redis.delete(*keys)


@pytest.fixture
async def client(app_instance, _clean_db_and_redis):
    """绑定到 app 的 httpx AsyncClient（用后自动清 DB/Redis）。"""
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def make_jwt():
    """签发测试 JWT（默认 admin，1 小时有效）。"""
    secret = get_settings().jwt_secret

    def _make(role: str = "admin", exp_delta: int = 3600, sub: str = "u1") -> str:
        payload = {"sub": sub, "role": role, "exp": int(time.time()) + exp_delta}
        return jwt.encode(payload, secret, algorithm="HS256")

    return _make
```

> session 级 `app_instance` 需 pytest-asyncio 的 event loop 覆盖到 session 作用域。`asyncio_mode = "auto"`（Task 1）下，session 级 async fixture 会自动获得 session 级 loop；若运行时报 loop scope 不匹配，在 `pyproject.toml` 的 `[tool.pytest.ini_options]` 加 `asyncio_default_fixture_loop_scope = "session"`。

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: 全部 PASS（含 Task 13 的 health/whoami）。需 docker-compose 服务运行 + `.env` 就位。

- [ ] **Step 4: 手动起服务验证**

Run: `uv run uvicorn app.main:app --port 8000` （另开终端）
Run: `curl -s localhost:8000/health | jq`
Expected: `{"code":0,"data":{"status":"healthy","db":"ok","redis":"ok"}}`
Run: `curl -s localhost:8000/metrics`
Expected: `# mo-chat-aiops metrics placeholder`

- [ ] **Step 5: Commit**

```bash
git add -f app/main.py tests/conftest.py
git commit -m "feat(app): assemble FastAPI app, lifespan, exception handlers, test fixtures"
```

---

### Task 15: 质量门 — ruff + mypy + 全量验证

**Files:** 无新增（修复 lint/类型问题）

- [ ] **Step 1: Ruff 检查并修复**

Run: `uv run ruff check . --fix`
Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 2: mypy 类型检查**

Run: `uv run mypy app`
Expected: 无 error（或仅可接受的 third-party 缺类型，已由 `ignore_missing_imports` 兜底）。修复实际类型问题。

- [ ] **Step 3: 全量测试**

Run: `uv run pytest -v`
Expected: 全绿。

- [ ] **Step 4: 清理临时文件**

确认无调试产物、无 `.pyc` 散落、`.env` 未被提交（`git status` 检查）。

- [ ] **Step 5: Commit**

```bash
git add -f app tests pyproject.toml
git commit -m "chore: pass ruff + mypy, finalize M0 foundation"
```

---

## 验收对照（对齐 spec §1.2）

- [ ] `uv run uvicorn app.main:app` 起服务成功（Task 14 Step 4）
- [ ] `/health` 真实连通 DB + Redis（Task 13/14）
- [ ] JWT 校验：合法 admin 过 / 过期 / 非 admin / 缺 token 拒（Task 8 + Task 13）
- [ ] `/metrics` 与健康检查可访问（Task 13）
- [ ] `uv run pytest` 全绿（Task 15）
- [ ] X-Project-Id 缺失/非法 → 4001（Task 9 + Task 13）
- [ ] Alembic 启动自动迁移建 projects 表（Task 12）

## Self-Review 备注

- **Spec 覆盖**：§4 各模块均有对应 Task（config→T2、constants→T3、logging→T5、db→T6、redis→T7、security→T8、project_context→T9、response→T4、models→T10、repositories 基类→T10b、health→T13、main→T14、alembic→T12、docker→T11）。§7 测试策略每项落到对应 test 文件。
- **类型一致性**：`success()`/`ApiResponse`/`ErrorResponse`/`APIError`/`Page`（T4）→ 被 T8/T9/T13 引用；`CurrentUser`（T8）→ T13 引用；`ping_db`/`ping_redis`（T6/T7）→ T13/T14 引用；`get_project_id`/`get_current_user`（T8/T9）→ T13 引用；`run_upgrade_head`（T12）→ T14 引用；`RedisKey`（T3）单点声明，M2+ 引用；`ProjectScopedRepository`（T10b）→ M1+ 业务仓库继承。命名前后一致。
- **依赖顺序**：T13 的测试依赖 T14 的 conftest，已在 T13 标注「随 T14 跑通」。
- **审查修复（子代理评审后）**：
  - B1/B2 lifespan 内 `asyncio.run()` 套娃 → 改 `await asyncio.to_thread(run_upgrade_head)`（T14 + migrate.py docstring + T12 测试）。
  - B3 测试无 DB 隔离 → 会话级 `app_instance` + `_clean_db_and_redis` TRUNCATE/清 Redis（T14 conftest）。
  - B4 `get_settings` lru_cache 污染 → autouse `_clear_settings_cache`（T14 conftest）。
  - B5 `CORS_ORIGINS=*` 崩 → Settings 加 `field_validator` 支持逗号分隔（T2）。
  - B6 迁移测试用未装的同步驱动 → 改 asyncpg async 断言（T12）。
  - B7 capsys 双调用 → 单次取全（T5）；python-json-logger pin `<3`（T1）。
  - A1 repositories 基类缺位 → 新增 T10b `ProjectScopedRepository`。
  - A3 加密密钥 Settings 留位 + `datasource_config: dict[str, Any]`（T2/T10）。
  - A4 响应裸 dict → 增 `ApiResponse[T]`/`ErrorResponse` 类型化信封供 OpenAPI（T4）。
  - A5 ErrorCode 分段规约 + A6 RedisKey 后缀单点声明（T3）。
  - C3 RequestValidationError 覆盖说明（T13，不写空断言）。
