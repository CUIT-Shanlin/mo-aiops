from __future__ import annotations

import json
from typing import Any

from app.collectors.models import SpanSummary
from app.collectors.windows import TraceCache
from app.core.constants import RedisKey


def serialize_traces(snapshot: list[tuple[str, list[SpanSummary]]]) -> str:
    payload = [
        {"traceId": trace_id, "spans": [span.model_dump() for span in spans]}
        for trace_id, spans in snapshot
    ]
    return json.dumps(payload, ensure_ascii=False)


def deserialize_traces(raw: Any) -> list[tuple[str, list[SpanSummary]]]:
    if raw is None:
        return []
    if isinstance(raw, bytes):
        raw = raw.decode()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    result: list[tuple[str, list[SpanSummary]]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        trace_id = str(item.get("traceId") or "")
        spans_raw = item.get("spans")
        if not trace_id or not isinstance(spans_raw, list):
            continue
        spans = [SpanSummary(**span) for span in spans_raw if isinstance(span, dict)]
        result.append((trace_id, spans))
    return result


async def save_trace_snapshot(
    redis: Any,
    project_id: str,
    snapshot: list[tuple[str, list[SpanSummary]]],
    *,
    ttl: int | None = None,
) -> None:
    if redis is None:
        return
    key = RedisKey.of(project_id, RedisKey.TRACES)
    await redis.set(key, serialize_traces(snapshot), ex=ttl)


async def load_trace_snapshot(
    redis: Any,
    trace_cache: TraceCache | None,
    project_id: str,
) -> list[tuple[str, list[SpanSummary]]]:
    if trace_cache is not None:
        memory = list(trace_cache.snapshot(project_id))
        if memory:
            return memory
    if redis is None or not hasattr(redis, "get"):
        return []
    try:
        raw = await redis.get(RedisKey.of(project_id, RedisKey.TRACES))
    except Exception:
        return []
    return deserialize_traces(raw)
