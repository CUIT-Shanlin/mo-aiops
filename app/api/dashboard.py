"""Dashboard summary REST APIs."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.alerts import _alert_to_dict, require_project_id
from app.core.constants import RedisKey
from app.core.security import CurrentUser, get_current_user
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.alerts import AlertEventRepository
from app.repositories.healing import HealActionRepository
from app.schemas.response import success

router = APIRouter(tags=["dashboard"])


@router.get("/api/v1/dashboard/stats")
async def dashboard_stats(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        alert_stats = await AlertEventRepository(session, project_id).stats()
        healing_stats = await HealActionRepository(session, project_id).stats()
    metrics = _latest_metrics(request, project_id)
    return success(
        {
            "alertCount": alert_stats["active"],
            "criticalCount": alert_stats["critical"],
            "recoveredCount": alert_stats["resolved"],
            "healingSuccessRate": healing_stats["successRate"],
            "tcpConnections": int(metrics.get("conn.active", 0)),
            "messageTps": int(metrics.get("msg.throughput", 0)),
            "mqBacklog": int(metrics.get("mq.backlog", 0)),
            "p99Latency": int(metrics.get("msg.p99_latency", 0)),
        }
    )


@router.get("/api/v1/dashboard/health-score")
async def dashboard_health_score(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        alert_stats = await AlertEventRepository(session, project_id).stats()
    score = max(
        0,
        100 - alert_stats["critical"] * 25 - alert_stats["bySeverity"].get("warning", 0) * 10,
    )
    return success({"score": score, "trend": [{"time": _short_time(), "score": score}]})


@router.get("/api/v1/dashboard/metrics/series")
async def dashboard_metric_series(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    metrics = _latest_metrics(request, project_id)
    return success(
        [
            _series("消息吞吐量 (msg/s)", "area", metrics.get("msg.throughput", 0)),
            _series("TCP 连接数", "area", metrics.get("conn.active", 0)),
            _series("P99 延迟 (ms)", "line", metrics.get("msg.p99_latency", 0)),
            _series("JVM Heap 使用率 (%)", "line", metrics.get("runtime.memory_used_ratio", 0)),
        ]
    )


@router.get("/api/v1/dashboard/alerts/recent")
async def dashboard_recent_alerts(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        result = await AlertEventRepository(session, project_id).search(page=1, page_size=limit)
    return success([_alert_to_dict(alert) for alert in result.items])


@router.get("/api/v1/dashboard/events")
async def dashboard_events(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        alerts = await AlertEventRepository(session, project_id).search(page=1, page_size=limit)
    return success(
        [
            {
                "time": _format_time(alert.fired_at),
                "type": "alert",
                "title": alert.name,
                "description": f"{alert.service or 'unknown'} {alert.severity}",
            }
            for alert in alerts.items
        ]
    )


@router.get("/api/v1/dashboard/ai-agent/status")
async def dashboard_agent_status(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    redis_status = await _redis_agent_status(request, project_id)
    async with request.app.state.sessionmaker() as session:
        latest = await AgentRunRepository(session, project_id).latest_completed()
    return success(
        {
            "status": redis_status.get("status") or "idle",
            "currentTask": redis_status.get("currentTask") or "",
            "relatedAlert": latest.anomaly_type if latest else None,
            "analyzedLogs": latest.analyzed_logs_count if latest else 0,
            "linkedTraces": latest.related_traces_count if latest else 0,
            "latestConclusion": latest.root_cause_summary if latest else "",
            "confidenceScore": latest.confidence if latest and latest.confidence else 0,
        }
    )


@router.get("/api/v1/dashboard/service-chain")
async def dashboard_service_chain(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        alerts = await AlertEventRepository(session, project_id).list_active_for_grouping()
    services = sorted({alert.service or "unknown" for alert in alerts}) or ["gateway", "message-service"]
    return success(
        [
            {
                "name": service,
                "status": "healthy" if not alerts else "analyzing",
                "qps": "0/s",
                "errorRate": "0%",
                "p99": "0ms",
                "alerts": sum(1 for alert in alerts if (alert.service or "unknown") == service),
            }
            for service in services
        ]
    )


def _latest_metrics(request: Request, project_id: str) -> dict[str, float]:
    store = getattr(request.app.state, "metric_window_store", None)
    if store is None:
        return {}
    return {sample.canonicalName: sample.value for sample in store.snapshot(project_id)}


def _series(title: str, chart_type: str, value: float) -> dict[str, Any]:
    rounded = round(float(value), 2)
    return {
        "title": title,
        "type": chart_type,
        "data": [{"time": _short_time(), "value": rounded, "baseline": rounded}],
    }


async def _redis_agent_status(request: Request, project_id: str) -> dict[str, Any]:
    raw = await request.app.state.redis.get(RedisKey.of(project_id, RedisKey.AGENT_STATUS))
    if raw is None:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {"status": str(raw)}
    return parsed if isinstance(parsed, dict) else {}


def _short_time() -> str:
    return datetime.now(UTC).strftime("%H:%M")


def _format_time(value: datetime | None) -> str:
    if value is None:
        return _short_time()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%H:%M")
