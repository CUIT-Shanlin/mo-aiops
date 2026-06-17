"""Healing action persistence model."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:
    """Return timezone-aware UTC now for ORM defaults."""
    return datetime.now(UTC)


class HealAction(Base):
    """Project-scoped healing action lifecycle record."""

    __tablename__ = "heal_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    alert_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_resource: Mapped[str] = mapped_column(String(255), nullable=False)
    target_namespace: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    operator: Mapped[str] = mapped_column(String(64), nullable=False)

    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approver_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


Index("ix_heal_actions_project_status", HealAction.project_id, HealAction.status)
Index("ix_heal_actions_project_created", HealAction.project_id, HealAction.created_at.desc())
Index(
    "uq_heal_actions_project_alert_action",
    HealAction.project_id,
    HealAction.alert_event_id,
    HealAction.action_type,
    unique=True,
    postgresql_where=HealAction.alert_event_id.is_not(None),
)
