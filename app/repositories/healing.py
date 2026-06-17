"""Project-scoped healing action repository."""
from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Generic, TypeVar, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.alert_event import AlertEvent
from app.models.heal_action import HealAction
from app.repositories.base import ProjectScopedRepository

T = TypeVar("T")


@dataclass(slots=True)
class PageResult(Generic[T]):
    """Minimal pagination result used by repositories."""

    total: int
    page: int
    size: int
    items: list[T]


@dataclass(slots=True)
class HealActionCreate:
    """Input payload for creating a healing action."""

    action_type: str
    target_resource: str
    status: str
    risk_level: str
    operator: str
    alert_event_id: int | None = None
    target_namespace: str | None = None
    approved_by: str | None = None
    approver_note: str | None = None
    result_message: str | None = None
    retry_count: int = 0
    executed_at: datetime | None = None
    finished_at: datetime | None = None


class HealActionRepository(ProjectScopedRepository):
    """CRUD/search operations for one project's healing actions."""

    project_column = cast(ColumnElement[Any], HealAction.project_id)

    def __init__(self, session: AsyncSession, project_id: str) -> None:
        super().__init__(session, project_id)
        self.session: AsyncSession = session

    async def create(self, data: HealActionCreate) -> HealAction:
        action = HealAction(
            project_id=self.project_id,
            alert_event_id=data.alert_event_id,
            action_type=data.action_type,
            target_resource=data.target_resource,
            target_namespace=data.target_namespace,
            status=data.status,
            risk_level=data.risk_level,
            operator=data.operator,
            approved_by=data.approved_by,
            approver_note=data.approver_note,
            result_message=data.result_message,
            retry_count=data.retry_count,
        )
        if data.executed_at is not None:
            action.executed_at = data.executed_at
        if data.finished_at is not None:
            action.finished_at = data.finished_at
        self.session.add(action)
        await self.session.flush()
        return action

    async def get(self, action_id: int) -> HealAction | None:
        result = await self.session.execute(
            self.scope(select(HealAction).where(HealAction.id == action_id))
        )
        return result.scalar_one_or_none()

    async def get_for_update(self, action_id: int) -> HealAction | None:
        """Return one project-scoped action with a row lock and fresh state."""
        result = await self.session.execute(
            self.scope(select(HealAction).where(HealAction.id == action_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, data: HealActionCreate) -> HealAction:
        if data.alert_event_id is not None:
            result = await self.session.execute(
                self.scope(
                    select(HealAction).where(
                        HealAction.alert_event_id == data.alert_event_id,
                        HealAction.action_type == data.action_type,
                    )
                )
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                return existing
        return await self.create(data)

    async def list(self, *, page: int = 1, page_size: int = 20) -> PageResult[HealAction]:
        offset = (page - 1) * page_size
        total_result = await self.session.execute(
            self.scope(select(func.count()).select_from(HealAction))
        )
        rows_result = await self.session.execute(
            self.scope(select(HealAction))
            .order_by(HealAction.created_at.desc(), HealAction.id.desc())
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
        risk_level: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PageResult[HealAction]:
        """Search project-scoped healing actions with frontend filters."""
        stmt = self.scope(select(HealAction))
        if status:
            stmt = stmt.where(HealAction.status == status)
        if risk_level:
            stmt = stmt.where(HealAction.risk_level == risk_level)

        offset = (page - 1) * page_size
        total_result = await self.session.execute(
            select(func.count()).select_from(stmt.subquery())
        )
        rows_result = await self.session.execute(
            stmt.order_by(HealAction.created_at.asc(), HealAction.id.asc())
            .offset(offset)
            .limit(page_size)
        )
        return PageResult(
            total=int(total_result.scalar_one()),
            page=page,
            size=page_size,
            items=list(rows_result.scalars().all()),
        )

    async def pending_approval(self) -> HealAction | None:
        """Return the oldest waiting approval action for this project."""
        result = await self.session.execute(
            self.scope(
                select(HealAction).where(HealAction.status == "waiting_approval")
            ).order_by(HealAction.created_at.asc(), HealAction.id.asc())
        )
        return result.scalars().first()

    async def stats(self) -> dict[str, float | int]:
        """Return MVP healing counters for one project."""
        today_start = datetime.combine(
            datetime.now(UTC).date(),
            time.min,
            tzinfo=UTC,
        )
        today_end = today_start + timedelta(days=1)
        rows = (
            await self.session.execute(
                self.scope(
                    select(HealAction.status, func.count())
                    .select_from(HealAction)
                    .where(
                        HealAction.created_at >= today_start,
                        HealAction.created_at < today_end,
                    )
                    .group_by(HealAction.status)
                )
            )
        ).all()
        by_status = {str(status): int(count) for status, count in rows}
        total = sum(by_status.values())
        success_count = by_status.get("success", 0) + by_status.get("verified", 0)
        failed_count = by_status.get("failed", 0)
        return {
            "todayTotal": total,
            "successCount": success_count,
            "failedCount": failed_count,
            "successRate": round((success_count / total) * 100, 1) if total else 0.0,
        }

    async def alert_names_by_action_ids(
        self, action_ids: builtins.list[int]
    ) -> dict[int, str]:
        """Map healing action ids to linked alert names within this project."""
        if not action_ids:
            return {}
        result = await self.session.execute(
            select(HealAction.id, AlertEvent.name)
            .join(AlertEvent, AlertEvent.id == HealAction.alert_event_id)
            .where(
                HealAction.project_id == self.project_id,
                AlertEvent.project_id == self.project_id,
                HealAction.id.in_(action_ids),
            )
        )
        return {int(action_id): str(name) for action_id, name in result.all()}
