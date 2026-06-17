"""Audit log REST API."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.alerts import require_project_id
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.exports.service import ExportStore, rows_to_csv, rows_to_json
from app.models.audit_log import AuditLog
from app.repositories.audit import AuditLogRepository
from app.schemas.response import APIError, success


router = APIRouter(tags=["audit"])


class ExportRequest(BaseModel):
    format: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


@router.get("/api/v1/audit/stats")
async def audit_stats(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        data = await AuditLogRepository(session, project_id).stats_today()
    return success(data)


@router.get("/api/v1/audit/logs")
async def list_audit_logs(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    keyword: str | None = Query(default=None),
    operator_type: str | None = Query(default=None, alias="operatorType"),
    action: str | None = Query(default=None),
    time_range: str | None = Query(default=None, alias="timeRange"),
    start_time: datetime | None = Query(default=None, alias="startTime"),
    end_time: datetime | None = Query(default=None, alias="endTime"),
    result: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    start_filter, end_filter = _resolve_time_range(
        time_range,
        _as_utc(start_time),
        _as_utc(end_time),
    )
    async with request.app.state.sessionmaker() as session:
        page_result = await AuditLogRepository(session, project_id).search(
            keyword=keyword,
            operator_type=operator_type,
            action=action,
            start_time=start_filter,
            end_time=end_filter,
            result=result,
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


@router.get("/api/v1/audit/logs/{audit_id}")
async def get_audit_log(
    audit_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        row = await AuditLogRepository(session, project_id).get(audit_id)
    if row is None:
        raise APIError(ErrorCode.NOT_FOUND, "审计日志不存在")
    return success(_audit_to_dict(row))


@router.post("/api/v1/audit/export")
async def export_audit_logs(
    payload: ExportRequest,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    export_format = payload.format.casefold()
    if export_format not in {"csv", "json"}:
        raise APIError(ErrorCode.VALIDATION_ERROR, "参数校验失败")

    params = payload.params
    start_filter, end_filter = _resolve_time_range(
        _as_optional_str(params.get("timeRange")),
        _as_utc(_parse_datetime_param(params.get("startTime"))),
        _as_utc(_parse_datetime_param(params.get("endTime"))),
    )
    async with request.app.state.sessionmaker() as session:
        result = await AuditLogRepository(session, project_id).search(
            keyword=_as_optional_str(params.get("keyword")),
            operator_type=_as_optional_str(params.get("operatorType")),
            action=_as_optional_str(params.get("action")),
            start_time=start_filter,
            end_time=end_filter,
            result=_as_optional_str(params.get("result")),
            page=1,
            page_size=10000,
        )

    rows = [_audit_to_dict(row) for row in result.items]
    if export_format == "csv":
        content = rows_to_csv(
            rows,
            [
                "id",
                "operatorType",
                "operator",
                "action",
                "targetObject",
                "targetResource",
                "result",
                "ipAddress",
                "timestamp",
                "details",
            ],
        )
        filename = "audit-export.csv"
        content_type = "text/csv; charset=utf-8"
    else:
        content = rows_to_json(rows)
        filename = "audit-export.json"
        content_type = "application/json; charset=utf-8"

    artifact = await ExportStore(request.app.state.redis, project_id).save(
        content=content,
        filename=filename,
        content_type=content_type,
    )
    return success({"downloadUrl": artifact.download_url})


def _audit_to_dict(row: AuditLog) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "operatorType": row.operator_type,
        "operator": row.operator_name,
        "action": row.action,
        "targetObject": row.resource_type,
        "targetResource": row.resource_id,
        "result": row.result,
        "ipAddress": row.ip_address,
        "timestamp": _format_datetime(row.created_at),
        "details": _audit_details(row),
    }


def _audit_details(row: AuditLog) -> str:
    if row.reason:
        return row.reason
    if row.before_state or row.after_state:
        return f"before={row.before_state or {}} after={row.after_state or {}}"
    return ""


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    utc_value = _as_utc(value)
    if utc_value is None:
        return None
    return utc_value.isoformat().replace("+00:00", "Z")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _resolve_time_range(
    time_range: str | None,
    start_time: datetime | None,
    end_time: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    if time_range in {None, "custom"}:
        return start_time, end_time

    now = datetime.now(UTC)
    if time_range == "24h":
        return now - timedelta(hours=24), end_time
    if time_range == "7d":
        return now - timedelta(days=7), end_time
    if time_range == "30d":
        return now - timedelta(days=30), end_time
    raise APIError(ErrorCode.VALIDATION_ERROR, "参数校验失败")


def _parse_datetime_param(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise APIError(ErrorCode.VALIDATION_ERROR, "参数校验失败")
    if value == "":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise APIError(ErrorCode.VALIDATION_ERROR, "参数校验失败") from exc


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
