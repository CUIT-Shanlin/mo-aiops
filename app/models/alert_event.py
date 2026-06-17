"""Alert event persistence model."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:
    """Return timezone-aware UTC now for ORM defaults."""
    return datetime.now(UTC)


class AlertEvent(Base):
    """Project-scoped alert event with update-style active deduplication."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    service: Mapped[str | None] = mapped_column(String(255), nullable=True)
    namespace: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pod: Mapped[str | None] = mapped_column(String(255), nullable=True)

    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    annotations: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    agent_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    related_heal_action_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    alert_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


Index("ix_alert_events_project_status", AlertEvent.project_id, AlertEvent.status)
Index("ix_alert_events_project_created", AlertEvent.project_id, AlertEvent.created_at.desc())
Index(
    "uq_alert_events_active_project_fingerprint",
    AlertEvent.project_id,
    AlertEvent.fingerprint,
    unique=True,
    postgresql_where=AlertEvent.status.in_(("firing", "processing", "healing")),
)
