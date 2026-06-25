"""Logs query service backed by Redis recent error cache."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis

from app.core.constants import RedisKey


@dataclass(slots=True)
class LogFilters:
    keyword: str | None = None
    service: str | None = None
    level: str | None = None
    trace_id: str | None = None
    time_range: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class LogsService:
    """Read and filter recent logs for one project."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def search(
        self,
        project_id: str,
        filters: LogFilters,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        logs = await self._filtered_logs(project_id, filters)
        counts = self._level_counts(logs)
        offset = (page - 1) * page_size
        return {
            "total": len(logs),
            **counts,
            "page": page,
            "pageSize": page_size,
            "items": logs[offset : offset + page_size],
        }

    async def histogram(
        self,
        project_id: str,
        filters: LogFilters,
        *,
        buckets: int = 20,
    ) -> list[dict[str, Any]]:
        logs = await self._filtered_logs(project_id, filters)
        buckets = max(1, buckets)
        window_start, window_end = self._histogram_window(logs, filters)
        parsed = [(self._parse_time(log.get("timestamp")), log) for log in logs]
        timed = [(ts, log) for ts, log in parsed if ts is not None]

        start = window_start
        end = window_end
        total_seconds = max((end - start).total_seconds(), 1.0)
        width = total_seconds / buckets
        result: list[dict[str, Any]] = [
            {
                "time": (start + timedelta(seconds=width * i))
                .isoformat()
                .replace("+00:00", "Z"),
                "count": 0,
                "errorCount": 0,
            }
            for i in range(buckets)
        ]
        for ts, log in timed:
            if ts < start or ts > end:
                continue
            index = min(int((ts - start).total_seconds() / width), buckets - 1)
            result[index]["count"] += 1
            if self._level(log) == "error":
                result[index]["errorCount"] += 1
        return result

    async def services(self, project_id: str) -> list[str]:
        logs = await self._read_recent_logs(project_id)
        return sorted(
            {
                str(log["service"])
                for log in logs
                if isinstance(log.get("service"), str) and log["service"]
            }
        )

    async def _filtered_logs(
        self, project_id: str, filters: LogFilters
    ) -> list[dict[str, Any]]:
        logs = await self._read_recent_logs(project_id)
        start, end = self._resolve_time_filters(filters)
        return [
            log
            for log in logs
            if self._matches(log, filters, start_time=start, end_time=end)
        ]

    async def _read_recent_logs(self, project_id: str) -> list[dict[str, Any]]:
        key = RedisKey.of(project_id, RedisKey.RECENT_LOGS)
        logs = await self._read_key(key)
        if logs:
            return logs
        # 回退到 ERROR/WARN 缓存，兼容仅有 recent_errors 的旧数据
        return await self._read_key(RedisKey.of(project_id, RedisKey.RECENT_ERRORS))

    async def _read_key(self, key: str) -> list[dict[str, Any]]:
        try:
            raw = await self.redis.get(key)
        except Exception:
            raw = None
        if raw is not None:
            return self._parse_redis_value(raw)

        try:
            entries = await self.redis.lrange(key, 0, -1)
        except Exception:
            entries = []
        logs: list[dict[str, Any]] = []
        for entry in entries:
            logs.extend(self._parse_redis_value(entry))
        return logs

    def _parse_redis_value(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, bytes):
            value = value.decode()
        if isinstance(value, dict):
            return [dict(value)]
        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                return []
            return self._parse_redis_value(loaded)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        return []

    def _matches(
        self,
        log: dict[str, Any],
        filters: LogFilters,
        *,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> bool:
        if filters.keyword:
            needle = filters.keyword.casefold()
            haystacks = (
                str(log.get("message", "")),
                str(log.get("traceId", "")),
                str(log.get("stack", "")),
                str(log.get("exception", "")),
            )
            if not any(needle in value.casefold() for value in haystacks):
                return False
        if filters.service and log.get("service") != filters.service:
            return False
        if filters.level and self._level(log) != filters.level.casefold():
            return False
        if filters.trace_id and log.get("traceId") != filters.trace_id:
            return False

        ts = self._parse_time(log.get("timestamp"))
        if ts is None:
            return start_time is None and end_time is None
        if start_time is not None and ts < start_time:
            return False
        if end_time is not None and ts > end_time:
            return False
        return True

    def _level_counts(self, logs: list[dict[str, Any]]) -> dict[str, int]:
        counts = {"errorCount": 0, "warnCount": 0, "infoCount": 0, "debugCount": 0}
        for log in logs:
            level = self._level(log)
            if level == "error":
                counts["errorCount"] += 1
            elif level in {"warn", "warning"}:
                counts["warnCount"] += 1
            elif level == "info":
                counts["infoCount"] += 1
            elif level == "debug":
                counts["debugCount"] += 1
        return counts

    def _resolve_time_filters(
        self, filters: LogFilters
    ) -> tuple[datetime | None, datetime | None]:
        start = self._parse_time(filters.start_time)
        end = self._parse_time(filters.end_time)
        if not filters.time_range or filters.time_range == "custom":
            return start, end

        deltas = {
            "15m": timedelta(minutes=15),
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
            "6h": timedelta(hours=6),
            "24h": timedelta(hours=24),
        }
        delta = deltas.get(filters.time_range)
        if delta is None:
            return start, end
        now = self._now()
        return now - delta, end or now

    def _histogram_window(
        self, logs: list[dict[str, Any]], filters: LogFilters
    ) -> tuple[datetime, datetime]:
        start, end = self._resolve_time_filters(filters)
        if start is not None or end is not None:
            if end is None:
                end = self._now()
            if start is None:
                start = end - timedelta(hours=1)
            return start, end

        timed = [
            parsed
            for parsed in (self._parse_time(log.get("timestamp")) for log in logs)
            if parsed is not None
        ]
        if timed:
            return min(timed), max(timed)

        end = self._now()
        return end - timedelta(hours=1), end

    def _parse_time(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _level(self, log: dict[str, Any]) -> str:
        return str(log.get("level", "")).casefold()

    def _now(self) -> datetime:
        return datetime.now(UTC)
