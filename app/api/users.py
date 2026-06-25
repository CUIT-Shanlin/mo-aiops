"""User management proxy MVP APIs."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.api.alerts import require_project_id
from app.audit.service import AuditService
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.models.user import User
from app.repositories.audit import AuditLogRepository
from app.repositories.users import UserCreate, UserRepository
from app.schemas.response import APIError, success

router = APIRouter(tags=["users"])


class BanRequest(BaseModel):
    reason: str = Field(min_length=1)
    duration: Literal["1h", "24h", "7d", "permanent"]
    forceDisconnect: bool = False


class UnbanRequest(BaseModel):
    reason: str | None = None


class CreateUserRequest(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=255)
    email: str | None = None
    avatar: str | None = None
    riskLevel: str = "normal"
    onlineStatus: str = "offline"


class UpdateUserRequest(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = None
    avatar: str | None = None
    riskLevel: str | None = None


@router.get("/api/v1/users/stats")
async def user_stats(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        repo = UserRepository(session, project_id)
        await repo.ensure_admin()
        stats = await repo.stats()
        await session.commit()
    return success({**stats, "riskUsers": stats["highRisk"]})


@router.get("/api/v1/users")
async def list_users(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    keyword: str | None = Query(default=None),
    is_banned: bool | None = Query(default=None, alias="isBanned"),
    online_status: str | None = Query(default=None, alias="onlineStatus"),
    risk_level: str | None = Query(default=None, alias="riskLevel"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        repo = UserRepository(session, project_id)
        await repo.ensure_admin()
        result = await repo.search(
            keyword=keyword,
            is_banned=is_banned,
            online_status=online_status,
            risk_level=risk_level,
            page=page,
            page_size=page_size,
        )
        items = [_user_to_dict(user) for user in result.items]
        await session.commit()
    return success(
        {
            "total": result.total,
            "page": result.page,
            "pageSize": result.size,
            "items": items,
        }
    )


@router.get("/api/v1/users/{user_id}")
async def get_user(
    user_id: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        repo = UserRepository(session, project_id)
        await repo.ensure_admin()
        user = await repo.get(user_id)
        if user is None:
            raise APIError(ErrorCode.NOT_FOUND, "用户不存在")
        data = _user_to_dict(user)
        await session.commit()
    return success(data)


@router.post("/api/v1/users")
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        repo = UserRepository(session, project_id)
        await repo.ensure_admin()
        try:
            user = await repo.create(
                UserCreate(
                    id=payload.id,
                    username=payload.username,
                    email=payload.email,
                    avatar=payload.avatar,
                    online_status=payload.onlineStatus,
                    risk_level=payload.riskLevel,
                )
            )
        except IntegrityError as exc:
            await session.rollback()
            raise APIError(ErrorCode.VALIDATION_ERROR, "用户 ID 已存在") from exc
        await AuditService(AuditLogRepository(session, project_id)).record(
            operator_type="admin",
            operator_name=current_user.user_id or current_user.role,
            operator_uid=current_user.user_id,
            action="USER_CREATE",
            resource_type="user",
            resource_id=payload.id,
            after_state={
                "userId": payload.id,
                "username": payload.username,
                "email": payload.email,
                "avatar": payload.avatar,
                "riskLevel": payload.riskLevel,
            },
            result="success",
        )
        data = _user_to_dict(user)
        await session.commit()
    return success(data)


@router.put("/api/v1/users/{user_id}")
async def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        repo = UserRepository(session, project_id)
        await repo.ensure_admin()
        before = await repo.get(user_id)
        if before is None:
            raise APIError(ErrorCode.NOT_FOUND, "用户不存在")
        before_state = {
            "username": before.username,
            "email": before.email,
            "avatar": before.avatar,
            "riskLevel": before.risk_level,
        }
        user = await repo.update(
            user_id,
            username=payload.username,
            email=payload.email,
            avatar=payload.avatar,
            risk_level=payload.riskLevel,
        )
        if user is None:
            raise APIError(ErrorCode.NOT_FOUND, "用户不存在")
        await AuditService(AuditLogRepository(session, project_id)).record(
            operator_type="admin",
            operator_name=current_user.user_id or current_user.role,
            operator_uid=current_user.user_id,
            action="USER_UPDATE",
            resource_type="user",
            resource_id=user_id,
            before_state=before_state,
            after_state={
                "username": user.username,
                "email": user.email,
                "avatar": user.avatar,
                "riskLevel": user.risk_level,
            },
            result="success",
        )
        data = _user_to_dict(user)
        await session.commit()
    return success(data)


@router.delete("/api/v1/users/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        repo = UserRepository(session, project_id)
        await repo.ensure_admin()
        deleted = await repo.delete(user_id)
        if not deleted:
            raise APIError(ErrorCode.NOT_FOUND, "用户不存在")
        await AuditService(AuditLogRepository(session, project_id)).record(
            operator_type="admin",
            operator_name=current_user.user_id or current_user.role,
            operator_uid=current_user.user_id,
            action="USER_DELETE",
            resource_type="user",
            resource_id=user_id,
            result="success",
        )
        await session.commit()
    return success({"success": True})


@router.post("/api/v1/users/{user_id}/ban")
async def ban_user(
    user_id: str,
    payload: BanRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        user_repo = UserRepository(session, project_id)
        await user_repo.ensure_admin()
        user = await user_repo.set_banned(user_id, True)
        if user is None:
            raise APIError(ErrorCode.NOT_FOUND, "用户不存在")
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
        user_repo = UserRepository(session, project_id)
        await user_repo.ensure_admin()
        user = await user_repo.set_banned(user_id, False)
        if user is None:
            raise APIError(ErrorCode.NOT_FOUND, "用户不存在")
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


def _user_to_dict(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "isBanned": user.is_banned,
        "onlineStatus": user.online_status,
        "riskLevel": user.risk_level,
        "todayMessages": user.today_messages,
        "lastLogin": _format_datetime(user.last_login_at),
        "registeredAt": user.registered_at.isoformat(),
        "avatar": user.avatar,
        "email": user.email,
    }


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
