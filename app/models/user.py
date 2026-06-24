"""Local user mirror persistence model."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:
    """Return timezone-aware UTC now for ORM defaults."""
    return datetime.now(UTC)


def utcdate() -> date:
    """Return current UTC date for ORM defaults."""
    return datetime.now(UTC).date()


class User(Base):
    """Project-scoped local user mirror from the monitored IM service."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_users_project_id_id"),
    )

    pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    id: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    online_status: Mapped[str] = mapped_column(String(32), nullable=False, default="offline")
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    today_messages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registered_at: Mapped[date] = mapped_column(Date, nullable=False, default=utcdate)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


Index("ix_users_project_id", User.project_id)
Index("ix_users_project_status_risk", User.project_id, User.online_status, User.risk_level)
Index("ix_users_project_username", User.project_id, User.username)
