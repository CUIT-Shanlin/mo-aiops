"""Audit log repository."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

T = TypeVar("T")


@dataclass(slots=True)
class PageResult(Generic[T]):
    """Minimal pagination result used by repositories."""

    total: int
    page: int
    size: int
    items: list[T]


@dataclass(slots=True)
class AuditLogCreate:
    """Input payload for creating an audit log."""

    operator_type: str
    operator_name: str
    action: str
    resource_type: str
    result: str
    operator_uid: str | None = None
    resource_id: str | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    ip_address: str | None = None
    reason: str | None = None


class AuditLogRepository:
    """Audit logs can be project-scoped or global when project_id is None."""

    def __init__(self, session: AsyncSession, project_id: str | None) -> None:
        self.session = session
        self.project_id = project_id

    def _scope(self, stmt):
        if self.project_id is None:
            return stmt.where(AuditLog.project_id.is_(None))
        return stmt.where(AuditLog.project_id == self.project_id)

    async def create(self, data: AuditLogCreate) -> AuditLog:
        row = AuditLog(
            project_id=self.project_id,
            operator_uid=data.operator_uid,
            operator_type=data.operator_type,
            operator_name=data.operator_name,
            action=data.action,
            resource_type=data.resource_type,
            resource_id=data.resource_id,
            before_state=data.before_state,
            after_state=data.after_state,
            ip_address=data.ip_address,
            reason=data.reason,
            result=data.result,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, audit_id: int) -> AuditLog | None:
        result = await self.session.execute(
            self._scope(select(AuditLog).where(AuditLog.id == audit_id))
        )
        return result.scalar_one_or_none()

    async def list(self, *, page: int = 1, page_size: int = 20) -> PageResult[AuditLog]:
        offset = (page - 1) * page_size
        total_result = await self.session.execute(
            self._scope(select(func.count()).select_from(AuditLog))
        )
        rows_result = await self.session.execute(
            self._scope(select(AuditLog))
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        return PageResult(
            total=int(total_result.scalar_one()),
            page=page,
            size=page_size,
            items=list(rows_result.scalars().all()),
        )

    async def stats_today(self) -> dict[str, int]:
        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)
        result = await self.session.execute(
            self._scope(
                select(
                    func.count(),
                    func.coalesce(
                        func.sum(
                            case((AuditLog.operator_type == "aiops_agent", 1), else_=0)
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(case((AuditLog.operator_type == "admin", 1), else_=0)),
                        0,
                    ),
                    func.coalesce(
                        func.sum(case((AuditLog.result == "failed", 1), else_=0)),
                        0,
                    ),
                ).where(
                    AuditLog.created_at >= today_start,
                    AuditLog.created_at < tomorrow_start,
                )
            )
        )
        total, aiops_agent_count, admin_count, failed_count = result.one()
        return {
            "todayTotal": int(total),
            "aiopsAgentCount": int(aiops_agent_count),
            "adminCount": int(admin_count),
            "failedCount": int(failed_count),
        }

    async def search(
        self,
        *,
        keyword: str | None = None,
        operator_type: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        result: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PageResult[AuditLog]:
        filters = []
        if keyword:
            pattern = f"%{keyword}%"
            filters.append(
                or_(
                    AuditLog.operator_name.ilike(pattern),
                    AuditLog.action.ilike(pattern),
                    AuditLog.resource_type.ilike(pattern),
                    AuditLog.resource_id.ilike(pattern),
                    AuditLog.reason.ilike(pattern),
                )
            )
        if operator_type:
            filters.append(AuditLog.operator_type == operator_type)
        if action:
            filters.append(AuditLog.action == action)
        if resource_id:
            filters.append(AuditLog.resource_id == resource_id)
        if result:
            filters.append(AuditLog.result == result)
        if start_time:
            filters.append(AuditLog.created_at >= start_time)
        if end_time:
            filters.append(AuditLog.created_at <= end_time)

        base = self._scope(select(AuditLog).where(*filters))
        offset = (page - 1) * page_size
        total_result = await self.session.execute(
            self._scope(select(func.count()).select_from(AuditLog).where(*filters))
        )
        rows_result = await self.session.execute(
            base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        return PageResult(
            total=int(total_result.scalar_one()),
            page=page,
            size=page_size,
            items=list(rows_result.scalars().all()),
        )
