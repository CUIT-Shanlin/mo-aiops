"""Frontend metrics REST APIs backed by the in-memory metric window."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.alerts import require_project_id
from app.collectors.windows import MetricWindowStore
from app.core.security import CurrentUser, get_current_user
from app.schemas.response import success

router = APIRouter(tags=["metrics"])

MetricCategory = tuple[str, list[tuple[str, str, str, int | None]]]

_METRIC_CATEGORIES: list[MetricCategory] = [
    (
        "系统资源",
        [
            ("sys.cpu", "CPU 使用率", "%", 80),
            ("sys.memory", "内存使用率", "%", 85),
            ("sys.network", "磁盘 I/O", "MB/s", 500),
        ],
    ),
    (
        "JVM 指标",
        [
            ("runtime.memory_used_ratio", "JVM Heap 使用率", "%", 85),
            ("runtime.gc_pause", "GC 耗时", "ms", 200),
        ],
    ),
    (
        "业务指标",
        [
            ("msg.throughput", "消息 TPS", "/s", 15000),
            ("conn.active", "TCP 连接数", "", 10000),
            ("msg.p99_latency", "P99 延迟", "ms", 100),
        ],
    ),
    (
        "数据库",
        [
            ("db.write_tps", "PostgreSQL TPS", "/s", 5000),
            ("cache.hit_ratio", "Redis 命中率", "%", None),
        ],
    ),
]


@router.get("/api/v1/metrics/categories")
async def metric_categories(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    values = _latest_metrics(request, project_id)
    return success(
        [
            {
                "category": category,
                "metrics": [
                    {
                        "category": category,
                        "name": name,
                        "value": str(values.get(canonical, 0)),
                        "unit": unit,
                        "status": _metric_status(values.get(canonical, 0), threshold),
                        "trend": 0,
                        "threshold": threshold,
                    }
                    for canonical, name, unit, threshold in metrics
                ],
            }
            for category, metrics in _METRIC_CATEGORIES
        ]
    )


@router.get("/api/v1/metrics/{name}/series")
async def metric_series(
    name: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    time_range: str | None = Query(default=None, alias="timeRange"),
    step: str | None = Query(default=None),
) -> dict[str, Any]:
    _ = (time_range, step)
    value = _latest_metrics(request, project_id).get(name, 0)
    return success([{"time": datetime.now(UTC).strftime("%H:%M"), "value": value, "baseline": value}])


def _latest_metrics(request: Request, project_id: str) -> dict[str, float]:
    store = getattr(request.app.state, "metric_window_store", None)
    if not isinstance(store, MetricWindowStore):
        return {}
    return {sample.canonicalName: sample.value for sample in store.snapshot(project_id)}


def _metric_status(value: float, threshold: int | None) -> str:
    if threshold is not None and value >= threshold:
        return "warning"
    return "normal"
