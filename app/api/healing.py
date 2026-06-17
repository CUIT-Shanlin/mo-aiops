"""Healing REST API."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.alerts import require_project_id
from app.audit.service import AuditService
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.healing.service import HealingService
from app.models.heal_action import HealAction
from app.repositories.alerts import AlertEventRepository
from app.repositories.audit import AuditLogRepository
from app.repositories.healing import HealActionRepository
from app.schemas.response import APIError, success


router = APIRouter(tags=["healing"])

_EXECUTOR_LABELS = {
    "admin": "管理员",
    "aiops_agent": "AI Agent",
}


class ApproveRequest(BaseModel):
    approver_note: str | None = Field(default=None, alias="approverNote")


class RejectRequest(BaseModel):
    reason: str


@router.get("/api/v1/healing/stats")
async def healing_stats(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        data = await HealActionRepository(session, project_id).stats()
    return success(data)


@router.get("/api/v1/healing/actions")
async def list_healing_actions(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    status: str | None = Query(default=None),
    risk_level: str | None = Query(default=None, alias="riskLevel"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        repository = HealActionRepository(session, project_id)
        result = await repository.search(
            status=status,
            risk_level=risk_level,
            page=page,
            page_size=page_size,
        )
        alert_names = await repository.alert_names_by_action_ids(
            [action.id for action in result.items]
        )
    return success(
        {
            "total": result.total,
            "page": result.page,
            "pageSize": result.size,
            "items": [
                _action_to_dict(action, alert_name=alert_names.get(action.id))
                for action in result.items
            ],
        }
    )


@router.get("/api/v1/healing/actions/pending-approval")
async def pending_approval(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        repository = HealActionRepository(session, project_id)
        action = await repository.pending_approval()
        if action is None:
            return success(None)
        alert_names = await repository.alert_names_by_action_ids([action.id])
    return success(_action_to_dict(action, alert_name=alert_names.get(action.id)))


@router.post("/api/v1/healing/actions/{action_id}/approve")
async def approve_healing_action(
    action_id: int,
    payload: ApproveRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        service = _service(session, project_id)
        try:
            await service.approve(
                action_id,
                approver_note=payload.approver_note,
                operator_uid=current_user.user_id or current_user.role,
            )
        except LookupError as exc:
            raise APIError(ErrorCode.NOT_FOUND, "自愈操作不存在") from exc
        except ValueError as exc:
            raise APIError(ErrorCode.VALIDATION_ERROR, "自愈操作状态无效") from exc
        await session.commit()
    return success({"success": True, "taskId": str(action_id)})


@router.post("/api/v1/healing/actions/{action_id}/reject")
async def reject_healing_action(
    action_id: int,
    payload: RejectRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        service = _service(session, project_id)
        try:
            await service.reject(
                action_id,
                reason=payload.reason,
                operator_uid=current_user.user_id or current_user.role,
            )
        except LookupError as exc:
            raise APIError(ErrorCode.NOT_FOUND, "自愈操作不存在") from exc
        except ValueError as exc:
            raise APIError(ErrorCode.VALIDATION_ERROR, "自愈操作状态无效") from exc
        await session.commit()
    return success({"success": True})


@router.get("/api/v1/healing/actions/{action_id}/status")
async def healing_action_status(
    action_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        action = await HealActionRepository(session, project_id).get(action_id)
    if action is None:
        raise APIError(ErrorCode.NOT_FOUND, "自愈操作不存在")
    return success(
        {
            "status": action.status,
            "result": action.result_message,
            "endTime": _format_datetime(action.finished_at),
            "duration": _format_duration(action),
        }
    )


def _service(session: Any, project_id: str) -> HealingService:
    return HealingService(
        HealActionRepository(session, project_id),
        AlertEventRepository(session, project_id),
        AuditService(AuditLogRepository(session, project_id)),
    )


def _action_to_dict(action: HealAction, *, alert_name: str | None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(action.id),
        "alertName": alert_name or "",
        "actionType": action.action_type,
        "targetResource": action.target_resource,
        "namespace": action.target_namespace,
        "status": action.status,
        "riskLevel": action.risk_level,
        "executor": _EXECUTOR_LABELS.get(action.operator, "AI Agent"),
        "startTime": _format_datetime(action.executed_at or action.created_at),
        "endTime": _format_datetime(action.finished_at),
        "duration": _format_duration(action),
        "result": action.result_message,
    }
    if action.status == "waiting_approval":
        data["impactScope"] = "需人工确认影响范围"
        data["estimatedRecovery"] = "3-5 分钟"
    return data


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _format_duration(action: HealAction) -> str | None:
    start = action.executed_at or action.created_at
    end = action.finished_at
    if end is None:
        return None
    seconds = max(int((end - start).total_seconds()), 0)
    minutes, remainder = divmod(seconds, 60)
    if minutes == 0:
        return f"{remainder}秒"
    return f"{minutes}分{remainder}秒" if remainder else f"{minutes}分"
