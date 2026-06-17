"""Alertmanager webhook payload ingestion."""
from __future__ import annotations

import json
import logging
import asyncio
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.service import AlertIngest, AlertService
from app.core.constants import RedisKey
from app.core.projects import ProjectsConfig
from app.repositories.alerts import AlertEventRepository

log = logging.getLogger(__name__)


async def process_ingest_payload(
    session: AsyncSession,
    project_id: str,
    payload: dict[str, Any],
) -> dict[str, int]:
    """Convert Alertmanager payload alerts into project-scoped alert rows."""
    service = AlertService(AlertEventRepository(session, project_id))
    counts = {"created": 0, "updated": 0, "ignored": 0}

    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        return {"created": 0, "updated": 0, "ignored": 1}

    for raw_alert in alerts:
        ingest = _alertmanager_alert_to_ingest(raw_alert)
        if ingest is None:
            counts["ignored"] += 1
            continue
        row = await service.ingest(ingest)
        if row.alert_count <= 1:
            counts["created"] += 1
        else:
            counts["updated"] += 1
    return counts


class IngestConsumer:
    """Poll project-scoped ingest queues and persist webhook payloads."""

    def __init__(
        self,
        *,
        redis: Redis,
        sessionmaker: Any,
        projects_config: ProjectsConfig,
        timeout_seconds: int = 5,
    ) -> None:
        self.redis = redis
        self.sessionmaker = sessionmaker
        self.projects_config = projects_config
        self.timeout_seconds = timeout_seconds
        self._stopped = False

    async def stop(self) -> None:
        """Request graceful loop shutdown."""
        self._stopped = True

    async def run_forever(self) -> None:
        """Continuously consume enabled project ingest queues."""
        while not self._stopped:
            keys = [
                RedisKey.of(project_id, RedisKey.INGEST)
                for project_id, project in self.projects_config.projects.items()
                if project.enabled
            ]
            if not keys:
                await asyncio.sleep(self.timeout_seconds)
                continue
            item = await self.redis.brpop(keys, timeout=self.timeout_seconds)
            if item is None:
                continue
            raw_key, raw_payload = item
            project_id = _project_id_from_ingest_key(_decode(raw_key))
            if project_id is None:
                log.warning("skip ingest payload from unknown key")
                continue
            try:
                payload = json.loads(_decode(raw_payload))
                async with self.sessionmaker() as session:
                    await process_ingest_payload(session, project_id, payload)
                    await session.commit()
            except Exception:
                log.warning("failed to process ingest payload", exc_info=True)


def _alertmanager_alert_to_ingest(raw_alert: object) -> AlertIngest | None:
    if not isinstance(raw_alert, dict):
        return None

    labels = raw_alert.get("labels")
    if not isinstance(labels, dict):
        return None
    annotations = raw_alert.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {}

    alert_name = labels.get("alertname") or labels.get("alert_name")
    if not alert_name:
        return None

    status = str(raw_alert.get("status") or "firing").lower()
    if status not in {"firing", "resolved"}:
        return None

    severity = str(labels.get("severity") or raw_alert.get("severity") or "warning")
    starts_at = _parse_alertmanager_time(raw_alert.get("startsAt"))
    ends_at = _parse_alertmanager_time(raw_alert.get("endsAt"))
    now = datetime.now(UTC)

    normalized_labels = {str(key): value for key, value in labels.items()}
    normalized_annotations = {str(key): value for key, value in annotations.items()}
    normalized_annotations["_alertmanager"] = {
        "status": status,
        "generatorURL": raw_alert.get("generatorURL"),
        "fingerprint": raw_alert.get("fingerprint"),
    }

    return AlertIngest(
        name=str(alert_name),
        severity=severity,
        labels=normalized_labels,
        annotations=normalized_annotations,
        service=_string_or_none(labels.get("service")),
        namespace=_string_or_none(labels.get("namespace")),
        pod=_string_or_none(labels.get("pod")),
        fired_at=starts_at or now,
        last_seen_at=ends_at if status == "resolved" and ends_at else now,
    )


def _parse_alertmanager_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _project_id_from_ingest_key(key: str) -> str | None:
    prefix = "aiops:"
    suffix = f":{RedisKey.INGEST}"
    if not key.startswith(prefix) or not key.endswith(suffix):
        return None
    project_id = key[len(prefix) : -len(suffix)]
    return project_id or None


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
