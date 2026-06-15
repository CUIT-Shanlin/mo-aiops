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
