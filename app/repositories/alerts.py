"""Project-scoped alert event repository."""
from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar, cast

from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.alert_event import AlertEvent
from app.repositories.base import ProjectScopedRepository

T = TypeVar("T")

ACTIVE_ALERT_STATUSES = ("firing", "processing", "healing")


@dataclass(slots=True)
class PageResult(Generic[T]):
    """Minimal pagination result used by repositories."""

    total: int
    page: int
    size: int
    items: list[T]


@dataclass(slots=True)
class AlertEventCreate:
    """Input payload for creating an alert event."""

    name: str
    fingerprint: str
    severity: str
    status: str = "firing"
    service: str | None = None
    namespace: str | None = None
    pod: str | None = None
    labels: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)
    agent_run_id: int | None = None
    related_heal_action_id: int | None = None
    alert_count: int = 1
    fired_at: datetime | None = None
    last_seen_at: datetime | None = None
    resolved_at: datetime | None = None


class AlertEventRepository(ProjectScopedRepository):
    """CRUD/search operations for one project's alert events."""

    project_column = cast(ColumnElement[Any], AlertEvent.project_id)

    def __init__(self, session: AsyncSession, project_id: str) -> None:
        super().__init__(session, project_id)
        self.session: AsyncSession = session

    async def create(self, data: AlertEventCreate) -> AlertEvent:
        alert = AlertEvent(
            project_id=self.project_id,
            fingerprint=data.fingerprint,
            name=data.name,
            severity=data.severity,
            status=data.status,
            service=data.service,
            namespace=data.namespace,
            pod=data.pod,
            labels=data.labels,
            annotations=data.annotations,
            agent_run_id=data.agent_run_id,
            related_heal_action_id=data.related_heal_action_id,
            alert_count=data.alert_count,
        )
        if data.fired_at is not None:
            alert.fired_at = data.fired_at
        if data.last_seen_at is not None:
            alert.last_seen_at = data.last_seen_at
        if data.resolved_at is not None:
            alert.resolved_at = data.resolved_at
        self.session.add(alert)
        await self.session.flush()
        return alert

    async def create_or_update_active(self, data: AlertEventCreate) -> AlertEvent:
        stmt = insert(AlertEvent).values(
            project_id=self.project_id,
            fingerprint=data.fingerprint,
            name=data.name,
            severity=data.severity,
            status=data.status,
            service=data.service,
            namespace=data.namespace,
            pod=data.pod,
            labels=data.labels,
            annotations=data.annotations,
            agent_run_id=data.agent_run_id,
            related_heal_action_id=data.related_heal_action_id,
            alert_count=data.alert_count,
            fired_at=data.fired_at or func.now(),
            last_seen_at=data.last_seen_at or func.now(),
            resolved_at=data.resolved_at,
            created_at=func.now(),
            updated_at=func.now(),
        )
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=[AlertEvent.project_id, AlertEvent.fingerprint],
            index_where=AlertEvent.status.in_(ACTIVE_ALERT_STATUSES),
            set_={
                "name": stmt.excluded.name,
                "severity": stmt.excluded.severity,
                "status": stmt.excluded.status,
                "service": stmt.excluded.service,
                "namespace": stmt.excluded.namespace,
                "pod": stmt.excluded.pod,
                "labels": stmt.excluded.labels,
                "annotations": stmt.excluded.annotations,
                "agent_run_id": func.coalesce(
                    stmt.excluded.agent_run_id, AlertEvent.agent_run_id
                ),
                "related_heal_action_id": func.coalesce(
                    stmt.excluded.related_heal_action_id,
                    AlertEvent.related_heal_action_id,
                ),
                "alert_count": AlertEvent.alert_count + 1,
                "last_seen_at": stmt.excluded.last_seen_at,
                "updated_at": func.now(),
            },
        ).returning(AlertEvent.id)
        alert_id = (await self.session.execute(upsert_stmt)).scalar_one()
        alert = await self.session.get(
            AlertEvent,
            alert_id,
            populate_existing=True,
        )
        if alert is None:  # pragma: no cover - protected by RETURNING id
            raise LookupError(f"alert {alert_id} not found after upsert")
        return alert

    async def get(self, alert_id: int) -> AlertEvent | None:
        result = await self.session.execute(
            self.scope(select(AlertEvent).where(AlertEvent.id == alert_id))
        )
        return result.scalar_one_or_none()

    async def active_by_fingerprint(self, fingerprint: str) -> AlertEvent | None:
        result = await self.session.execute(
            self.scope(
                select(AlertEvent).where(
                    AlertEvent.fingerprint == fingerprint,
                    AlertEvent.status.in_(ACTIVE_ALERT_STATUSES),
                )
            ).order_by(AlertEvent.created_at.desc())
        )
        return result.scalars().first()

    async def list(self, *, page: int = 1, page_size: int = 20) -> PageResult[AlertEvent]:
        offset = (page - 1) * page_size
        total_result = await self.session.execute(
            self.scope(select(func.count()).select_from(AlertEvent))
        )
        rows_result = await self.session.execute(
            self.scope(select(AlertEvent))
            .order_by(AlertEvent.created_at.desc(), AlertEvent.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        return PageResult(
            total=int(total_result.scalar_one()),
            page=page,
            size=page_size,
            items=list(rows_result.scalars().all()),
        )

    async def search(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        service: str | None = None,
        namespace: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PageResult[AlertEvent]:
        """Search project-scoped alerts with frontend filters."""
        stmt = self._filtered_select(
            status=status,
            severity=severity,
            service=service,
            namespace=namespace,
            keyword=keyword,
        )
        offset = (page - 1) * page_size
        total_result = await self.session.execute(
            select(func.count()).select_from(stmt.subquery())
        )
        rows_result = await self.session.execute(
            stmt.order_by(AlertEvent.fired_at.desc(), AlertEvent.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        return PageResult(
            total=int(total_result.scalar_one()),
            page=page,
            size=page_size,
            items=list(rows_result.scalars().all()),
        )

    async def stats(self) -> dict[str, Any]:
        """Return status and severity counters for one project."""
        status_rows = (
            await self.session.execute(
                self.scope(
                    select(AlertEvent.status, func.count())
                    .select_from(AlertEvent)
                    .group_by(AlertEvent.status)
                )
            )
        ).all()
        severity_rows = (
            await self.session.execute(
                self.scope(
                    select(AlertEvent.severity, func.count())
                    .select_from(AlertEvent)
                    .group_by(AlertEvent.severity)
                )
            )
        ).all()
        by_status = {str(status): int(count) for status, count in status_rows}
        by_severity = {str(severity): int(count) for severity, count in severity_rows}
        total = sum(by_status.values())
        duplicate_count = await self._duplicate_count()
        received_count = total + duplicate_count
        return {
            "total": total,
            "critical": by_severity.get("critical", 0),
            "resolved": by_status.get("resolved", 0),
            "dedupRate": (
                round((duplicate_count / received_count) * 100, 1)
                if received_count
                else 0.0
            ),
            "active": sum(by_status.get(status, 0) for status in ACTIVE_ALERT_STATUSES),
            "byStatus": by_status,
            "bySeverity": by_severity,
        }

    async def list_active_for_grouping(self) -> builtins.list[AlertEvent]:
        """Return active alerts for query-time grouping."""
        rows_result = await self.session.execute(
            self.scope(
                select(AlertEvent).where(AlertEvent.status.in_(ACTIVE_ALERT_STATUSES))
            ).order_by(AlertEvent.fired_at.asc(), AlertEvent.id.asc())
        )
        return list(rows_result.scalars().all())

    def _filtered_select(
        self,
        *,
        status: str | None,
        severity: str | None,
        service: str | None,
        namespace: str | None,
        keyword: str | None,
    ) -> Select:
        stmt = self.scope(select(AlertEvent))
        if status:
            stmt = stmt.where(AlertEvent.status == status)
        if severity:
            stmt = stmt.where(AlertEvent.severity == severity)
        if service:
            stmt = stmt.where(AlertEvent.service == service)
        if namespace:
            stmt = stmt.where(AlertEvent.namespace == namespace)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    AlertEvent.name.ilike(pattern),
                    AlertEvent.service.ilike(pattern),
                    AlertEvent.namespace.ilike(pattern),
                    AlertEvent.pod.ilike(pattern),
                )
            )
        return stmt

    async def _duplicate_count(self) -> int:
        result = await self.session.execute(
            self.scope(
                select(func.coalesce(func.sum(AlertEvent.alert_count - 1), 0))
                .select_from(AlertEvent)
                .where(AlertEvent.alert_count > 1)
            )
        )
        return int(result.scalar_one())
