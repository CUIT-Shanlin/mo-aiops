from __future__ import annotations

import math
from typing import Any, Iterable, Protocol

from app.collectors.models import MetricSample
from app.collectors.windows import MetricWindowStore
from app.core.projects import ProjectConfig
from app.metrics_profile import get_profile


class PrometheusProvider(Protocol):
    async def query(self, **kwargs: Any) -> Any:
        ...

DEFAULT_CANONICAL_METRICS = (
    "runtime.memory_used_ratio",
    "runtime.gc_pause",
    "http.error_rate",
    "sys.cpu",
    "sys.memory",
    "jvm.threads",
    "jvm.classes_loaded",
)


def extract_prometheus_value(payload: Any) -> float | None:
    """从 Prometheus vector response 提取第一个可用 sample。"""
    if not isinstance(payload, dict):
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    result = data.get("result", [])
    if not isinstance(result, list):
        return None
    if not result:
        return None

    sample = result[0]
    if not isinstance(sample, dict):
        return None

    value = sample.get("value")
    if not isinstance(value, list) or len(value) < 2:
        return None

    try:
        number = float(value[1])
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None
    return number


async def collect_project_metrics(
    *,
    project_id: str,
    project: ProjectConfig,
    provider: PrometheusProvider,
    window_store: MetricWindowStore,
    canonical_metrics: Iterable[str] = DEFAULT_CANONICAL_METRICS,
) -> list[MetricSample]:
    """按 canonical 指标采集 Prometheus instant 值并写窗口。"""
    profile = get_profile(project.metric_profile)
    samples: list[MetricSample] = []

    for canonical_name in canonical_metrics:
        promql = profile.resolve(canonical_name)
        try:
            payload = await provider.query(query_type="instant", query=promql)
        except Exception:
            continue
        value = extract_prometheus_value(payload)
        if value is None:
            continue
        samples.append(window_store.add(project_id, canonical_name, value))

    return samples
