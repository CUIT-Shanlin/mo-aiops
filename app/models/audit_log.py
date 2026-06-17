"""Audit log persistence model."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:
    """Return timezone-aware UTC now for ORM defaults."""
    return datetime.now(UTC)


class AuditLog(Base):
    """Audit log record. project_id is nullable for global operations."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    operator_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    operator_type: Mapped[str] = mapped_column(String(32), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(128), nullable=False)

    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


Index("ix_audit_logs_project_created", AuditLog.project_id, AuditLog.created_at.desc())
Index("ix_audit_logs_action_created", AuditLog.action, AuditLog.created_at.desc())
