"""Agent run persistence model."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def utcnow() -> datetime:
    """Return timezone-aware UTC now for ORM defaults."""
    return datetime.now(UTC)


class AgentRun(Base):
    """Project-scoped LangGraph Agent execution record."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
    alert_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")

    fault_service: Mapped[str | None] = mapped_column(String(255), nullable=True)
    anomaly_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(
        Numeric(5, 2, asdecimal=False), nullable=True
    )
    root_cause_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    action_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auto_heal: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(32), nullable=True)

    node_states: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_chain: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    rag_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    llm_calls_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    analyzed_logs_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    related_traces_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


Index("ix_agent_runs_project_created", AgentRun.project_id, AgentRun.created_at.desc())
Index(
    "ix_agent_runs_project_service_type",
    AgentRun.project_id,
    AgentRun.fault_service,
    AgentRun.anomaly_type,
)
