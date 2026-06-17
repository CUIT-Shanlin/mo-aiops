"""Notifications REST API."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.alerts import require_project_id
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.models.notification import Notification
from app.notifications.service import NotificationService
from app.repositories.notifications import NotificationRepository
from app.schemas.response import APIError, success


router = APIRouter(tags=["notifications"])


@router.get("/api/v1/notifications")
async def list_notifications(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        service = NotificationService(NotificationRepository(session, project_id))
        unread_count, items = await service.list_recent(limit=limit)
    return success(
        {
            "unreadCount": unread_count,
            "items": [_notification_to_dict(item) for item in items],
        }
    )


@router.post("/api/v1/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        service = NotificationService(NotificationRepository(session, project_id))
        try:
            await service.mark_read(notification_id)
        except LookupError as exc:
            raise APIError(ErrorCode.NOT_FOUND, "通知不存在") from exc
        await session.commit()
    return success({"success": True})


def _notification_to_dict(row: Notification) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "type": row.type,
        "title": row.title,
        "message": row.message,
        "time": _format_datetime(row.created_at),
        "read": row.read,
    }


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
