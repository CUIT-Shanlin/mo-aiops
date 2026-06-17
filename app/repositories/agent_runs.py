"""Project-scoped repository for Agent run persistence."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.agent_run import AgentRun
from app.repositories.base import ProjectScopedRepository


def utcnow() -> datetime:
    """Return timezone-aware UTC now for repository timestamp updates."""
    return datetime.now(UTC)


@dataclass(slots=True)
class AgentRunCreate:
    """Input payload for creating a project-scoped Agent run."""

    trigger_source: str
    status: str = "running"
    alert_event_id: int | None = None
    fault_service: str | None = None
    anomaly_type: str | None = None
    severity: str | None = None
    confidence: float | None = None
    root_cause_summary: str | None = None
    action_type: str | None = None
    target_resource: str | None = None
    auto_heal: bool | None = None
    risk_level: str | None = None
    node_states: dict[str, Any] = field(default_factory=dict)
    evidence_chain: dict[str, Any] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    rag_results: list[dict[str, Any]] = field(default_factory=list)
    llm_calls_count: int = 0
    analyzed_logs_count: int = 0
    related_traces_count: int = 0


class AgentRunRepository(ProjectScopedRepository):
    """CRUD/search operations scoped to one project_id."""

    project_column = cast(ColumnElement[Any], AgentRun.project_id)

    def __init__(self, session: AsyncSession, project_id: str) -> None:
        super().__init__(session, project_id)
        self.session: AsyncSession = session

    async def create(self, data: AgentRunCreate) -> AgentRun:
        run = AgentRun(
            project_id=self.project_id,
            trigger_source=data.trigger_source,
            alert_event_id=data.alert_event_id,
            status=data.status,
            fault_service=data.fault_service,
            anomaly_type=data.anomaly_type,
            severity=data.severity,
            confidence=data.confidence,
            root_cause_summary=data.root_cause_summary,
            action_type=data.action_type,
            target_resource=data.target_resource,
            auto_heal=data.auto_heal,
            risk_level=data.risk_level,
            node_states=data.node_states,
            evidence_chain=data.evidence_chain,
            timeline=data.timeline,
            rag_results=data.rag_results,
            llm_calls_count=data.llm_calls_count,
            analyzed_logs_count=data.analyzed_logs_count,
            related_traces_count=data.related_traces_count,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def get(self, run_id: int) -> AgentRun | None:
        result = await self.session.execute(
            self.scope(select(AgentRun).where(AgentRun.id == run_id))
        )
        return result.scalar_one_or_none()

    async def list_completed(self, *, limit: int = 20) -> list[AgentRun]:
        stmt = (
            self.scope(select(AgentRun).where(AgentRun.status == "completed"))
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_failed(self, *, limit: int = 20) -> list[AgentRun]:
        stmt = (
            self.scope(select(AgentRun).where(AgentRun.status == "failed"))
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest_completed(self) -> AgentRun | None:
        stmt = (
            self.scope(select(AgentRun).where(AgentRun.status == "completed"))
            .order_by(AgentRun.finished_at.desc().nullslast(), AgentRun.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def latest_run(self) -> AgentRun | None:
        stmt = (
            self.scope(select(AgentRun))
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_created_since(self, since: datetime) -> int:
        result = await self.session.execute(
            self.scope(select(func.count()).select_from(AgentRun).where(AgentRun.created_at >= since))
        )
        return int(result.scalar_one())

    async def finish(
        self,
        run_id: int,
        *,
        status: str,
        conclusion: dict[str, Any] | None = None,
        healing_decision: dict[str, Any] | None = None,
        node_states: dict[str, Any] | None = None,
        evidence_chain: dict[str, Any] | None = None,
        timeline: list[dict[str, Any]] | None = None,
        rag_results: list[dict[str, Any]] | None = None,
        stats: dict[str, int] | None = None,
    ) -> AgentRun | None:
        run = await self.get(run_id)
        if run is None:
            return None

        run.status = status
        run.finished_at = utcnow()
        run.updated_at = run.finished_at

        if conclusion:
            run.fault_service = conclusion.get("fault_service")
            run.anomaly_type = conclusion.get("anomaly_type")
            run.severity = conclusion.get("severity")
            run.confidence = conclusion.get("confidence")
            run.root_cause_summary = conclusion.get("root_cause_summary")

        if healing_decision:
            run.action_type = healing_decision.get("action_type")
            run.target_resource = healing_decision.get("target_resource")
            run.auto_heal = healing_decision.get("auto_heal")
            run.risk_level = healing_decision.get("risk_level")

        if node_states is not None:
            run.node_states = node_states
        if evidence_chain is not None:
            run.evidence_chain = evidence_chain
        if timeline is not None:
            run.timeline = timeline
        if rag_results is not None:
            run.rag_results = rag_results

        if stats:
            run.llm_calls_count = stats.get("llm_calls_count", run.llm_calls_count)
            run.analyzed_logs_count = stats.get(
                "analyzed_logs_count", run.analyzed_logs_count
            )
            run.related_traces_count = stats.get(
                "related_traces_count", run.related_traces_count
            )

        await self.session.flush()
        return run
