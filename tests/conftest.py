"""async 测试 fixtures：app、AsyncClient、签 JWT、DB/缓存隔离。"""
import time

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.projects import reset_projects_config


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch):
    """每个测试前后清 lru_cache，防 monkeypatch env 跨测试污染。"""
    monkeypatch.setenv("STARTUP_PROVIDER_VALIDATION", "false")
    monkeypatch.setenv("STARTUP_INGEST_ENABLED", "false")
    get_settings.cache_clear()
    reset_projects_config()
    yield
    get_settings.cache_clear()
    reset_projects_config()


@pytest.fixture
async def app_instance():
    """函数级 app：每测跑一次 lifespan（建 engine/redis + 幂等迁移）。

    刻意不用 session 作用域：session 级 async fixture 与 function 级
    client/_clean 跨事件循环会触发 "Event loop is closed"。迁移幂等，
    每测重建换来 engine/redis 与测试同循环，正确性优先于速度。
    """
    from app.main import get_app

    application = get_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def _clean_db_and_redis(app_instance):
    """测试后清测试 Redis key，保证集成测试间状态隔离。"""
    yield
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
