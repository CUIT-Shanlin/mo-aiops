"""Settings REST API."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.api.alerts import require_project_id
from app.api.audit import _audit_to_dict
from app.audit.service import AuditService
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.repositories.audit import AuditLogRepository
from app.schemas.response import APIError, success
from app.settings.service import SettingsService


router = APIRouter(tags=["settings"])


class UpdateConfigRequest(BaseModel):
    value: str
    confirm: bool = False


@router.get("/api/v1/settings/categories")
async def list_categories(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    return success(_service(request, project_id).categories())


@router.get("/api/v1/settings/configs")
async def list_configs(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    category: str | None = Query(default=None),
) -> dict[str, Any]:
    return success(await _service(request, project_id).list_configs(category=category))


@router.put("/api/v1/settings/configs/{key}")
async def update_config(
    key: str,
    payload: UpdateConfigRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    service = _service(request, project_id)
    definition = service.get_definition(key)
    if definition is None:
        raise APIError(ErrorCode.NOT_FOUND, "配置项不存在")
    if definition.risk_level == "high" and not payload.confirm:
        raise APIError(ErrorCode.VALIDATION_ERROR, "参数校验失败")
    before_value = await service.get_effective_value(key)
    if before_value is None:
        raise APIError(ErrorCode.NOT_FOUND, "配置项不存在")

    before_state = {"value": before_value}
    after_state = {"value": payload.value}

    await _record_config_audit(
        request=request,
        project_id=project_id,
        current_user=current_user,
        key=key,
        before_state=before_state,
        after_state=after_state,
        result="requested",
    )
    try:
        await service.write_override(key, payload.value)
    except Exception as exc:
        await _record_config_audit(
            request=request,
            project_id=project_id,
            current_user=current_user,
            key=key,
            before_state=before_state,
            after_state=after_state,
            result="failed",
            reason=str(exc),
        )
        raise APIError(ErrorCode.EXTERNAL_SERVICE, "配置更新失败") from exc

    try:
        await _record_config_audit(
            request=request,
            project_id=project_id,
            current_user=current_user,
            key=key,
            before_state=before_state,
            after_state=after_state,
            result="success",
        )
    except Exception:
        pass
    return success({"success": True})


@router.get("/api/v1/settings/history")
async def settings_history(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        page_result = await AuditLogRepository(session, project_id).search(
            action="CONFIG_UPDATE",
            page=page,
            page_size=page_size,
        )
    return success(
        {
            "total": page_result.total,
            "page": page_result.page,
            "pageSize": page_result.size,
            "items": [_audit_to_dict(row) for row in page_result.items],
        }
    )


def _service(request: Request, project_id: str) -> SettingsService:
    return SettingsService(request.app.state.redis, project_id)


async def _record_config_audit(
    *,
    request: Request,
    project_id: str,
    current_user: CurrentUser,
    key: str,
    before_state: dict[str, str],
    after_state: dict[str, str],
    result: str,
    reason: str | None = None,
) -> None:
    async with request.app.state.sessionmaker() as session:
        audit_service = AuditService(AuditLogRepository(session, project_id))
        await audit_service.record(
            operator_type=current_user.role,
            operator_name=current_user.role,
            operator_uid=current_user.user_id,
            action="CONFIG_UPDATE",
            resource_type="config",
            resource_id=key,
            before_state=before_state,
            after_state=after_state,
            result=result,
            reason=reason,
        )
        await session.commit()
