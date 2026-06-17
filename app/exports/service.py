from __future__ import annotations

import csv
import io
import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from app.core.constants import RedisKey


EXPORT_TTL_SECONDS = 3600


@dataclass(slots=True)
class ExportArtifact:
    export_id: str
    filename: str
    content_type: str
    content: str

    @property
    def download_url(self) -> str:
        return f"/api/v1/exports/{self.export_id}/download"


class ExportStore:
    def __init__(self, redis: Redis, project_id: str) -> None:
        self.redis = redis
        self.project_id = project_id

    async def save(
        self,
        *,
        content: str,
        filename: str,
        content_type: str,
    ) -> ExportArtifact:
        export_id = secrets.token_urlsafe(16)
        key = RedisKey.of(self.project_id, f"exports:{export_id}")
        await self.redis.hset(
            key,
            mapping={
                "content": content,
                "filename": filename,
                "content_type": content_type,
            },
        )
        await self.redis.expire(key, EXPORT_TTL_SECONDS)
        return ExportArtifact(
            export_id=export_id,
            filename=filename,
            content_type=content_type,
            content=content,
        )

    async def get(self, export_id: str) -> ExportArtifact | None:
        key = RedisKey.of(self.project_id, f"exports:{export_id}")
        row = await self.redis.hgetall(key)
        if not row:
            return None
        normalized_row = {
            _decode_redis_value(field): _decode_redis_value(value)
            for field, value in row.items()
        }
        return ExportArtifact(
            export_id=export_id,
            filename=normalized_row["filename"],
            content_type=normalized_row["content_type"],
            content=normalized_row["content"],
        )


def _decode_redis_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def rows_to_csv(rows: Iterable[dict[str, Any]], columns: list[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _escape_csv_cell(value) for key, value in row.items()})
    return output.getvalue()


def rows_to_json(rows: Iterable[dict[str, Any]]) -> str:
    return json.dumps(list(rows), ensure_ascii=False, indent=2, default=str)


def _escape_csv_cell(value: Any) -> Any:
    if not isinstance(value, str) or value == "":
        return value
    if value[0] in {"=", "+", "-", "@", "\t", "\r", "\n"}:
        return f"'{value}"
    return value
