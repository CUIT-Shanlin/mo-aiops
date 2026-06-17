"""Saved log query persistence model."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:
    """Return timezone-aware UTC now for ORM defaults."""
    return datetime.now(UTC)


class SavedQuery(Base):
    """Project-scoped saved log query."""

    __tablename__ = "saved_queries"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "name",
            name="uq_saved_queries_project_name",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


Index(
    "ix_saved_queries_project_created",
    SavedQuery.project_id,
    SavedQuery.created_at.desc(),
)
