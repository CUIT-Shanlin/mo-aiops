"""CORS 中间件行为：通配源反射 + 凭证 + 暴露头。"""
import pytest


@pytest.fixture
def build_app(monkeypatch):
    """按给定 CORS_ORIGINS 构建 app（清配置缓存）。"""
    from app.core.config import get_settings

    def _build(origins: str):
        monkeypatch.setenv("CORS_ORIGINS", origins)
        get_settings.cache_clear()
        from app.main import get_app

        return get_app()

    return _build


def _cors_headers(resp):
    return {
        k.lower(): v
        for k, v in resp.headers.items()
        if k.lower().startswith("access-control") or k.lower() == "vary"
    }


async def test_wildcard_reflects_origin_with_credentials(build_app):
    """CORS_ORIGINS=* 时反射具体 Origin 并允许凭证（不输出字面 *）。"""
    from httpx import ASGITransport, AsyncClient

    app = build_app("*")
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # 预检：无需 lifespan 状态，OPTIONS 直接由中间件应答
            pre = await c.options(
                "/health",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "authorization,x-project-id",
                },
            )
            h = _cors_headers(pre)
            assert pre.status_code == 200
            assert h["access-control-allow-origin"] == "http://localhost:5173"
            assert h["access-control-allow-credentials"] == "true"
            assert "GET" in h["access-control-allow-methods"]

            # 实际请求
            actual = await c.get(
                "/health", headers={"Origin": "http://localhost:5173"}
            )
            h2 = _cors_headers(actual)
            assert h2["access-control-allow-origin"] == "http://localhost:5173"
            assert h2["access-control-allow-credentials"] == "true"
            assert "X-Request-Id" in h2["access-control-expose-headers"]


async def test_explicit_origins_allowlist(build_app):
    """显式源列表只放行白名单内的 Origin。"""
    from httpx import ASGITransport, AsyncClient

    app = build_app("http://localhost:5173,https://console.example.com")
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            ok = await c.get("/health", headers={"Origin": "http://localhost:5173"})
            h_ok = _cors_headers(ok)
            assert h_ok["access-control-allow-origin"] == "http://localhost:5173"
            assert h_ok["access-control-allow-credentials"] == "true"

            bad = await c.get(
                "/health", headers={"Origin": "http://evil.example.com"}
            )
            h_bad = _cors_headers(bad)
            assert "access-control-allow-origin" not in h_bad
