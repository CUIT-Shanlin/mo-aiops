from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from app.collectors.models import LogEntry
from app.core.constants import RedisKey

RECENT_ERRORS_TTL_SECONDS = 600
RECENT_ERRORS_LIMIT = 100
RECENT_LOGS_TTL_SECONDS = 600
RECENT_LOGS_LIMIT = 300


class LokiProvider(Protocol):
    async def query(self, **kwargs: Any) -> Any:
        ...


class RedisClient(Protocol):
    async def set(self, name: str, value: str, ex: int | None = None) -> Any:
        ...


def _timestamp_from_loki_ns(value: Any) -> str:
    try:
        seconds = int(value) / 1_000_000_000
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat().replace("+00:00", "Z")


def _parse_log_line(line: Any) -> dict[str, Any]:
    if not isinstance(line, str):
        return {}
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return {"message": line}
    return parsed if isinstance(parsed, dict) else {"message": line}


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def normalize_loki_streams(payload: Any) -> list[LogEntry]:
    """把 Loki streams 转成前端和 Agent 可消费的日志条目。"""
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    if not isinstance(data, dict):
        return []

    result = data.get("result")
    if not isinstance(result, list):
        return []

    entries: list[LogEntry] = []
    for stream_item in result:
        if not isinstance(stream_item, dict):
            continue

        labels = stream_item.get("stream")
        if not isinstance(labels, dict):
            labels = {}

        values = stream_item.get("values")
        if not isinstance(values, list):
            continue

        for item in values:
            if not isinstance(item, list | tuple) or len(item) < 2:
                continue
            timestamp_ns, line = item[0], item[1]
            parsed = _parse_log_line(line)
            if not parsed:
                continue

            message = parsed.get("message")
            if message is None:
                message = line if isinstance(line, str) else ""

            entries.append(
                LogEntry(
                    timestamp=_timestamp_from_loki_ns(timestamp_ns),
                    level=_as_text(parsed.get("level") or labels.get("level")),
                    service=_as_text(parsed.get("service") or labels.get("service")),
                    namespace=_as_text(
                        parsed.get("namespace") or labels.get("namespace")
                    ),
                    pod=_as_text(parsed.get("pod") or labels.get("pod")),
                    traceId=_as_text(parsed.get("traceId") or parsed.get("trace_id")),
                    spanId=_as_text(parsed.get("spanId") or parsed.get("span_id")),
                    message=_as_text(message)[:200],
                )
            )
    return entries


async def collect_project_logs(
    *,
    project_id: str,
    provider: LokiProvider,
    redis: RedisClient,
    query: str | None = None,
) -> tuple[list[LogEntry], set[str]]:
    """采集最近 ERROR/WARN 日志，写入 Redis 最近错误缓存。

    默认按 `project_id` 标签 scope，避免抓到其他项目/命名空间的日志。
    """
    if query is None:
        query = f'{{project_id="{project_id}",level=~"ERROR|WARN"}}'
    payload = await provider.query(query=query, limit=RECENT_ERRORS_LIMIT)
    entries = normalize_loki_streams(payload)[:RECENT_ERRORS_LIMIT]
    await redis.set(
        RedisKey.of(project_id, RedisKey.RECENT_ERRORS),
        json.dumps([entry.model_dump() for entry in entries], ensure_ascii=False),
        ex=RECENT_ERRORS_TTL_SECONDS,
    )
    trace_ids = {entry.traceId for entry in entries if entry.traceId}
    return entries, trace_ids


async def collect_project_recent_logs(
    *,
    project_id: str,
    provider: LokiProvider,
    redis: RedisClient,
    query: str | None = None,
) -> list[LogEntry]:
    """采集最近全级别日志，写入 Redis 全级别缓存，供前端日志检索页使用。

    与 `collect_project_logs` 不同：不按 level 过滤，覆盖 DEBUG/INFO/WARN/ERROR，
    供 `/api/v1/logs/*` 检索；Agent 分析仍只消费 ERROR/WARN 的 recent_errors。
    默认按 `project_id` 标签 scope，避免抓到其他项目/命名空间的日志。
    """
    if query is None:
        query = f'{{project_id="{project_id}"}}'
    payload = await provider.query(query=query, limit=RECENT_LOGS_LIMIT)
    entries = normalize_loki_streams(payload)[:RECENT_LOGS_LIMIT]
    await redis.set(
        RedisKey.of(project_id, RedisKey.RECENT_LOGS),
        json.dumps([entry.model_dump() for entry in entries], ensure_ascii=False),
        ex=RECENT_LOGS_TTL_SECONDS,
    )
    return entries
