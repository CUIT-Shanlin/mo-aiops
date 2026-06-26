from __future__ import annotations

import json
from typing import Any

from app.collectors.windows import MetricWindowStore
from app.core.constants import RedisKey


async def save_metric_snapshot(
    redis: Any,
    project_id: str,
    metrics: dict[str, float],
    *,
    ttl: int | None = None,
) -> None:
    if redis is None:
        return
    payload = {name: float(value) for name, value in metrics.items()}
    await redis.set(
        RedisKey.of(project_id, RedisKey.METRICS_SNAPSHOT),
        json.dumps(payload, ensure_ascii=False),
        ex=ttl,
    )


async def load_metric_values(
    redis: Any,
    store: MetricWindowStore | None,
    project_id: str,
) -> dict[str, float]:
    if store is not None:
        memory = {sample.canonicalName: sample.value for sample in store.snapshot(project_id)}
        if memory:
            return memory
    if redis is None or not hasattr(redis, "get"):
        return {}
    try:
        raw = await redis.get(RedisKey.of(project_id, RedisKey.METRICS_SNAPSHOT))
    except Exception:
        return {}
    if raw is None:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, float] = {}
    for name, value in parsed.items():
        try:
            result[str(name)] = float(value)
        except (ValueError, TypeError):
            pass
    return result
