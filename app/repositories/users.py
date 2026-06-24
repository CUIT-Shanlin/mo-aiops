"""Project-scoped local user repository."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Generic, TypeVar, cast

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.user import User
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
class UserCreate:
    """Input payload for creating a local user mirror row."""

    id: str | int
    username: str
    email: str | None = None
    avatar: str | None = None
    is_banned: bool = False
    online_status: str = "offline"
    risk_level: str = "low"
    today_messages: int = 0
    last_login_at: datetime | None = None
    registered_at: date | None = None


class UserRepository(ProjectScopedRepository):
    """CRUD/search operations for one project's local users."""

    project_column = cast(ColumnElement[Any], User.project_id)

    def __init__(self, session: AsyncSession, project_id: str) -> None:
        super().__init__(session, project_id)
        self.session: AsyncSession = session

    async def create(self, data: UserCreate) -> User:
        row = User(
            project_id=self.project_id,
            id=str(data.id),
            username=data.username,
            email=data.email,
            avatar=data.avatar,
            is_banned=data.is_banned,
            online_status=data.online_status,
            risk_level=data.risk_level,
            today_messages=data.today_messages,
            last_login_at=data.last_login_at,
        )
        if data.registered_at is not None:
            row.registered_at = data.registered_at
        self.session.add(row)
        await self.session.flush()
        return row

    async def ensure_admin(self, username: str = "admin") -> User:
        row = await self.get(username)
        if row is not None:
            return row
        return await self.create(
            UserCreate(
                id=username,
                username=username,
                email=None,
                avatar=None,
                online_status="offline",
                risk_level="normal",
                today_messages=0,
            )
        )

    async def get(self, user_id: str | int) -> User | None:
        result = await self.session.execute(
            self.scope(select(User).where(User.id == str(user_id)))
        )
        return result.scalar_one_or_none()

    async def search(
        self,
        *,
        keyword: str | None = None,
        is_banned: bool | None = None,
        online_status: str | None = None,
        risk_level: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PageResult[User]:
        stmt = self._filtered_select(
            keyword=keyword,
            is_banned=is_banned,
            online_status=online_status,
            risk_level=risk_level,
        )
        offset = (page - 1) * page_size
        total = await self._count(stmt)
        rows_result = await self.session.execute(
            stmt.order_by(User.username.asc(), User.id.asc()).offset(offset).limit(page_size)
        )
        return PageResult(
            total=total,
            page=page,
            size=page_size,
            items=list(rows_result.scalars().all()),
        )

    async def stats(self) -> dict[str, int]:
        total = await self._count(self.scope(select(User)))
        online = await self._count(
            self.scope(select(User).where(User.online_status == "online"))
        )
        banned = await self._count(self.scope(select(User).where(User.is_banned.is_(True))))
        high_risk = await self._count(
            self.scope(select(User).where(User.risk_level == "high"))
        )
        return {
            "total": total,
            "online": online,
            "banned": banned,
            "highRisk": high_risk,
        }

    async def set_banned(self, user_id: str | int, is_banned: bool) -> User | None:
        row = await self.get(user_id)
        if row is None:
            return None
        row.is_banned = is_banned
        await self.session.flush()
        return row

    async def _count(self, stmt: Select[tuple[User]]) -> int:
        result = await self.session.execute(select(func.count()).select_from(stmt.subquery()))
        return int(result.scalar_one())

    def _filtered_select(
        self,
        *,
        keyword: str | None,
        is_banned: bool | None,
        online_status: str | None,
        risk_level: str | None,
    ) -> Select[tuple[User]]:
        stmt = self.scope(select(User))
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    User.username.ilike(pattern),
                    User.id.ilike(pattern),
                )
            )
        if is_banned is not None:
            stmt = stmt.where(User.is_banned.is_(is_banned))
        if online_status is not None:
            stmt = stmt.where(User.online_status == online_status)
        if risk_level is not None:
            stmt = stmt.where(User.risk_level == risk_level)
        return stmt
