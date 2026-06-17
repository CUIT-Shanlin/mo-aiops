"""Project-scoped saved query repository."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.saved_query import SavedQuery
from app.repositories.base import ProjectScopedRepository


@dataclass(slots=True)
class SavedQueryCreate:
    """Input payload for saving a log query."""

    name: str
    params: dict[str, Any] = field(default_factory=dict)


class SavedQueryRepository(ProjectScopedRepository):
    """CRUD operations for one project's saved log queries."""

    project_column = cast(ColumnElement[Any], SavedQuery.project_id)

    def __init__(self, session: AsyncSession, project_id: str) -> None:
        super().__init__(session, project_id)
        self.session: AsyncSession = session

    async def create(self, data: SavedQueryCreate) -> SavedQuery:
        row = SavedQuery(
            project_id=self.project_id,
            name=data.name,
            params=data.params,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list(self) -> list[SavedQuery]:
        result = await self.session.execute(
            self.scope(select(SavedQuery)).order_by(
                SavedQuery.created_at.desc(), SavedQuery.id.desc()
            )
        )
        return list(result.scalars().all())
