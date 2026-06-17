"""Topology REST API."""
from __future__ import annotations

from collections import Counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.alerts import _alert_to_dict, require_project_id
from app.core.security import CurrentUser, get_current_user
from app.repositories.alerts import AlertEventRepository
from app.schemas.response import success
from app.topology.service import TopologyService


router = APIRouter(tags=["topology"])


@router.get("/api/v1/topology/graph")
async def topology_graph(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    graph_filter: str = Query(default="all", alias="filter", pattern="^(all|anomaly|alert)$"),
) -> dict[str, Any]:
    alert_counts = await _active_alert_counts(request, project_id)
    data = await _service(request).graph(
        project_id,
        graph_filter=graph_filter,
        alert_counts=alert_counts,
    )
    return success(data)


@router.get("/api/v1/topology/services/{service_id}")
async def topology_service_detail(
    service_id: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    alert_counts = await _active_alert_counts(request, project_id)
    data = await _service(request).service_detail(
        project_id,
        service_id,
        alert_counts=alert_counts,
    )
    return success(data)


@router.get("/api/v1/topology/services/{service_id}/metrics")
async def topology_service_metrics(
    service_id: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    return success(_service(request).service_metrics(project_id, service_id))


@router.get("/api/v1/topology/services/{service_id}/alerts")
async def topology_service_alerts(
    service_id: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        result = await AlertEventRepository(session, project_id).search(
            service=service_id,
            page=page,
            page_size=page_size,
        )
    return success(
        {
            "total": result.total,
            "page": result.page,
            "pageSize": result.size,
            "items": [_alert_to_dict(alert) for alert in result.items],
        }
    )


@router.get("/api/v1/topology/services/{service_id}/logs")
async def topology_service_logs(
    service_id: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    data = await _service(request).logs_for_service(
        project_id,
        service_id,
        page=page,
        page_size=page_size,
    )
    return success(data)


@router.post("/api/v1/topology/refresh")
async def topology_refresh(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    return success(await _service(request).refresh(project_id))


def _service(request: Request) -> TopologyService:
    return TopologyService(
        trace_cache=getattr(request.app.state, "trace_cache", None),
        metric_window_store=getattr(request.app.state, "metric_window_store", None),
        redis=getattr(request.app.state, "redis", None),
    )


async def _active_alert_counts(request: Request, project_id: str) -> dict[str, int]:
    async with request.app.state.sessionmaker() as session:
        alerts = await AlertEventRepository(session, project_id).list_active_for_grouping()
    return dict(Counter(alert.service for alert in alerts if alert.service))
