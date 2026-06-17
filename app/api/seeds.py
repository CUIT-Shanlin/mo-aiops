"""Development seed data API."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.alerts import require_project_id
from app.core.config import get_settings
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.schemas.response import APIError, success
from app.seeds.service import SeedService


router = APIRouter(prefix="/api/v1/seeds", tags=["seeds"])


class SeedDemoRequest(BaseModel):
    reset: bool = False


@router.post("/demo")
async def seed_demo(
    payload: SeedDemoRequest,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict:
    if get_settings().environment == "prod":
        raise APIError(ErrorCode.FORBIDDEN, "生产环境禁用种子数据接口")
    async with request.app.state.sessionmaker() as session:
        counts = await SeedService(session, request.app.state.redis).seed_demo(
            project_id,
            reset=payload.reset,
        )
    return success(counts)
