"""健康检查 / metrics 占位（探针不进 /api/v1 前缀）。"""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app.core.db import ping_db
from app.core.redis import ping_redis
from app.schemas.response import success

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    """探活 DB + Redis。"""
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
    """Prometheus 抓取端点（M0 占位）。"""
    return "# mo-chat-aiops metrics placeholder\n"
