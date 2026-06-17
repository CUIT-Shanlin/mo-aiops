"""Trace query REST APIs backed by the in-memory TraceCache."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.alerts import require_project_id
from app.collectors.models import SpanSummary
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.schemas.response import APIError, success

router = APIRouter(tags=["traces"])


@router.get("/api/v1/traces")
async def list_traces(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    trace_id: str | None = Query(default=None, alias="traceId"),
    service: str | None = Query(default=None),
    min_duration: float | None = Query(default=None, alias="minDuration"),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    rows = [
        _trace_summary(current_trace_id, spans)
        for current_trace_id, spans in _snapshot(request, project_id)
    ]
    rows = [
        row
        for row in rows
        if (trace_id is None or row["traceId"] == trace_id)
        and (service is None or row["rootService"] == service)
        and (min_duration is None or row["totalDuration"] >= min_duration)
        and (status is None or row["status"] == status)
    ]
    offset = (page - 1) * page_size
    return success(
        {
            "total": len(rows),
            "page": page,
            "pageSize": page_size,
            "items": rows[offset : offset + page_size],
        }
    )


@router.get("/api/v1/traces/services")
async def trace_services(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    services = sorted(
        {
            span.service
            for _, spans in _snapshot(request, project_id)
            for span in spans
            if span.service
        }
    )
    return success(services)


@router.get("/api/v1/traces/{trace_id}")
async def trace_detail(
    trace_id: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    spans = _get_trace(request, project_id, trace_id)
    return success({**_trace_summary(trace_id, spans), "spans": [_span_to_dict(span) for span in spans]})


@router.get("/api/v1/traces/{trace_id}/spans")
async def trace_spans(
    trace_id: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    spans = _get_trace(request, project_id, trace_id)
    offset = 0.0
    rows = []
    for span in spans:
        rows.append(
            {
                "name": span.name,
                "service": span.service,
                "duration": span.durationMs,
                "startOffset": offset,
                "status": span.status,
                "tags": {},
            }
        )
        offset += span.durationMs
    return success(rows)


def _snapshot(request: Request, project_id: str) -> list[tuple[str, list[SpanSummary]]]:
    cache = getattr(request.app.state, "trace_cache", None)
    if cache is None:
        return []
    return list(cache.snapshot(project_id))


def _get_trace(request: Request, project_id: str, trace_id: str) -> list[SpanSummary]:
    cache = getattr(request.app.state, "trace_cache", None)
    spans = cache.get(project_id, trace_id) if cache is not None else None
    if spans is None:
        raise APIError(ErrorCode.NOT_FOUND, "Trace 不存在")
    return spans


def _trace_summary(trace_id: str, spans: list[SpanSummary]) -> dict[str, Any]:
    total_duration = sum(span.durationMs for span in spans)
    error_count = sum(1 for span in spans if span.status == "error")
    root = spans[0] if spans else None
    return {
        "traceId": trace_id,
        "rootService": root.service if root else "",
        "entryPoint": root.name if root else "",
        "totalDuration": total_duration,
        "spanCount": len(spans),
        "errorSpanCount": error_count,
        "startTime": _format_datetime(datetime.now(UTC)),
        "status": "error" if error_count else "success",
    }


def _span_to_dict(span: SpanSummary) -> dict[str, Any]:
    return {
        "traceId": span.traceId,
        "spanId": span.spanId,
        "parentSpanId": span.parentSpanId,
        "service": span.service,
        "name": span.name,
        "durationMs": span.durationMs,
        "status": span.status,
    }


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
