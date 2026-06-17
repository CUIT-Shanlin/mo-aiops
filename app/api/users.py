"""User management proxy MVP APIs."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.alerts import require_project_id
from app.audit.service import AuditService
from app.core.security import CurrentUser, get_current_user
from app.repositories.audit import AuditLogRepository
from app.schemas.response import success

router = APIRouter(tags=["users"])


class BanRequest(BaseModel):
    reason: str = Field(min_length=1)
    duration: Literal["1h", "24h", "7d", "permanent"]
    forceDisconnect: bool = False


class UnbanRequest(BaseModel):
    reason: str | None = None


@router.get("/api/v1/users/stats")
async def user_stats(
    _request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    _project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    return success({"total": 0, "online": 0, "banned": 0, "riskUsers": 0})


@router.get("/api/v1/users")
async def list_users(
    _request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    _project_id: Annotated[str, Depends(require_project_id)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    return success({"total": 0, "page": page, "pageSize": page_size, "items": []})


@router.get("/api/v1/users/{user_id}")
async def get_user(
    user_id: str,
    _request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    _project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    return success(
        {
            "id": user_id,
            "username": user_id,
            "online": False,
            "banned": False,
            "privateMessages": None,
        }
    )


@router.post("/api/v1/users/{user_id}/ban")
async def ban_user(
    user_id: str,
    payload: BanRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        await AuditService(AuditLogRepository(session, project_id)).record(
            operator_type="admin",
            operator_name=current_user.user_id or current_user.role,
            operator_uid=current_user.user_id,
            action="USER_BAN",
            resource_type="user",
            resource_id=user_id,
            after_state={
                "userId": user_id,
                "duration": payload.duration,
                "forceDisconnect": payload.forceDisconnect,
            },
            reason=payload.reason,
            result="success",
        )
        await session.commit()
    return success({"success": True, "forceDisconnect": payload.forceDisconnect})


@router.post("/api/v1/users/{user_id}/unban")
async def unban_user(
    user_id: str,
    payload: UnbanRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        await AuditService(AuditLogRepository(session, project_id)).record(
            operator_type="admin",
            operator_name=current_user.user_id or current_user.role,
            operator_uid=current_user.user_id,
            action="USER_UNBAN",
            resource_type="user",
            resource_id=user_id,
            after_state={"userId": user_id},
            reason=payload.reason,
            result="success",
        )
        await session.commit()
    return success({"success": True})
