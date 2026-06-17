"""Project-scoped notification repository."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast
from typing import List as TypingList

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.notification import Notification
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
class NotificationCreate:
    """Input payload for creating a notification."""

    type: str
    title: str
    message: str
    read: bool = False


class NotificationRepository(ProjectScopedRepository):
    """CRUD/search operations for one project's notifications."""

    project_column = cast(ColumnElement[Any], Notification.project_id)

    def __init__(self, session: AsyncSession, project_id: str) -> None:
        super().__init__(session, project_id)
        self.session: AsyncSession = session

    async def create(self, data: NotificationCreate) -> Notification:
        row = Notification(
            project_id=self.project_id,
            type=data.type,
            title=data.title,
            message=data.message,
            read=data.read,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, notification_id: int) -> Notification | None:
        result = await self.session.execute(
            self.scope(select(Notification).where(Notification.id == notification_id))
        )
        return result.scalar_one_or_none()

    async def list(
        self, *, page: int = 1, page_size: int = 20
    ) -> PageResult[Notification]:
        offset = (page - 1) * page_size
        total_result = await self.session.execute(
            self.scope(select(func.count()).select_from(Notification))
        )
        rows_result = await self.session.execute(
            self.scope(select(Notification))
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset(offset)
            .limit(page_size)
        )
        return PageResult(
            total=int(total_result.scalar_one()),
            page=page,
            size=page_size,
            items=list(rows_result.scalars().all()),
        )

    async def list_recent(self, *, limit: int = 20) -> TypingList[Notification]:
        result = await self.session.execute(
            self.scope(select(Notification))
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def unread_count(self) -> int:
        result = await self.session.execute(
            self.scope(
                select(func.count()).select_from(Notification).where(Notification.read.is_(False))
            )
        )
        return int(result.scalar_one())

    async def mark_read(self, notification_id: int) -> Notification | None:
        row = await self.get(notification_id)
        if row is None:
            return None
        row.read = True
        await self.session.flush()
        return row

    async def mark_all_read(self) -> int:
        result = await self.session.execute(
            self.scope(select(Notification).where(Notification.read.is_(False)))
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.read = True
        await self.session.flush()
        return len(rows)
