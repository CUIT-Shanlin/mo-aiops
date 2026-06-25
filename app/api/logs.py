"""Logs REST API."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.api.alerts import require_project_id
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.exports.service import ExportStore, rows_to_csv, rows_to_json
from app.logs.service import LogFilters, LogsService
from app.models.saved_query import SavedQuery
from app.repositories.saved_queries import SavedQueryCreate, SavedQueryRepository
from app.schemas.response import APIError, success


router = APIRouter(tags=["logs"])


class SaveQueryRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    params: dict[str, Any] = Field(default_factory=dict)


class UpdateSavedQueryRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ExportRequest(BaseModel):
    format: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


@router.get("/api/v1/logs/search")
async def search_logs(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    keyword: str | None = Query(default=None),
    service: str | None = Query(default=None),
    level: str | None = Query(default=None),
    trace_id: str | None = Query(default=None, alias="traceId"),
    time_range: str | None = Query(default=None, alias="timeRange"),
    start_time: str | None = Query(default=None, alias="startTime"),
    end_time: str | None = Query(default=None, alias="endTime"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    data = await LogsService(request.app.state.redis).search(
        project_id,
        LogFilters(
            keyword=keyword,
            service=service,
            level=level,
            trace_id=trace_id,
            time_range=time_range,
            start_time=start_time,
            end_time=end_time,
        ),
        page=page,
        page_size=page_size,
    )
    return success(data)


@router.get("/api/v1/logs/histogram")
async def logs_histogram(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    keyword: str | None = Query(default=None),
    service: str | None = Query(default=None),
    level: str | None = Query(default=None),
    trace_id: str | None = Query(default=None, alias="traceId"),
    time_range: str | None = Query(default=None, alias="timeRange"),
    start_time: str | None = Query(default=None, alias="startTime"),
    end_time: str | None = Query(default=None, alias="endTime"),
    buckets: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    data = await LogsService(request.app.state.redis).histogram(
        project_id,
        LogFilters(
            keyword=keyword,
            service=service,
            level=level,
            trace_id=trace_id,
            time_range=time_range,
            start_time=start_time,
            end_time=end_time,
        ),
        buckets=buckets,
    )
    return success(data)


@router.get("/api/v1/logs/services")
async def log_services(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    return success(await LogsService(request.app.state.redis).services(project_id))


@router.post("/api/v1/logs/export")
async def export_logs(
    payload: ExportRequest,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    export_format = payload.format.casefold()
    if export_format not in {"csv", "json"}:
        raise APIError(ErrorCode.VALIDATION_ERROR, "参数校验失败")

    filters = _log_filters_from_params(payload.params)
    result = await LogsService(request.app.state.redis).search(
        project_id,
        filters,
        page=1,
        page_size=10000,
    )
    rows = [dict(item) for item in result["items"]]
    if export_format == "csv":
        content = rows_to_csv(
            rows,
            ["timestamp", "level", "service", "traceId", "message"],
        )
        filename = "logs-export.csv"
        content_type = "text/csv; charset=utf-8"
    else:
        content = rows_to_json(rows)
        filename = "logs-export.json"
        content_type = "application/json; charset=utf-8"

    artifact = await ExportStore(request.app.state.redis, project_id).save(
        content=content,
        filename=filename,
        content_type=content_type,
    )
    return success({"downloadUrl": artifact.download_url})


@router.post("/api/v1/logs/queries/save")
async def save_log_query(
    payload: SaveQueryRequest,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        try:
            row = await SavedQueryRepository(session, project_id).create(
                SavedQueryCreate(name=payload.name, params=payload.params)
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise APIError(ErrorCode.VALIDATION_ERROR, "查询名称已存在") from exc
    return success({"id": str(row.id)})


@router.get("/api/v1/logs/queries")
async def list_log_queries(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        rows = await SavedQueryRepository(session, project_id).list()
    return success([_saved_query_to_dict(row) for row in rows])


@router.put("/api/v1/logs/queries/{query_id}")
async def update_log_query(
    query_id: int,
    payload: UpdateSavedQueryRequest,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        repo = SavedQueryRepository(session, project_id)
        try:
            row = await repo.update(query_id, name=payload.name)
        except IntegrityError as exc:
            await session.rollback()
            raise APIError(ErrorCode.VALIDATION_ERROR, "查询名称已存在") from exc
        if row is None:
            raise APIError(ErrorCode.NOT_FOUND, "已保存查询不存在")
        data = _saved_query_to_dict(row)
        await session.commit()
    return success(data)


@router.delete("/api/v1/logs/queries/{query_id}")
async def delete_log_query(
    query_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        repo = SavedQueryRepository(session, project_id)
        deleted = await repo.delete(query_id)
        if not deleted:
            raise APIError(ErrorCode.NOT_FOUND, "已保存查询不存在")
        await session.commit()
    return success({"success": True})


def _saved_query_to_dict(row: SavedQuery) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "params": row.params,
        "createdAt": _format_datetime(row.created_at),
    }


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _log_filters_from_params(params: dict[str, Any]) -> LogFilters:
    return LogFilters(
        keyword=_as_optional_str(params.get("keyword")),
        service=_as_optional_str(params.get("service")),
        level=_as_optional_str(params.get("level")),
        trace_id=_as_optional_str(params.get("traceId")),
        time_range=_as_optional_str(params.get("timeRange")),
        start_time=_as_optional_str(params.get("startTime")),
        end_time=_as_optional_str(params.get("endTime")),
    )


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
