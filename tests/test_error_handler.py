"""兜底异常处理器：未捕获异常 → 统一 {code:5000} body，不漏 FastAPI 默认 {detail}。"""
from httpx import ASGITransport, AsyncClient

from app.core.constants import ErrorCode
from app.main import get_app


async def test_unhandled_exception_returns_unified_envelope():
    app = get_app()

    @app.get("/_boom")
    async def _boom() -> dict:
        raise RuntimeError("unexpected")

    # raise_app_exceptions=False：让已注册的 Exception 处理器接管，而非冒泡到测试
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/_boom")

    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == ErrorCode.INTERNAL  # 5000
    assert body["message"] == "internal error"
    assert body["detail"] is None
