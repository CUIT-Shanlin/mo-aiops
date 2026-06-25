from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import build_agent_graph
from app.agent.nodes import AgentNodeContext
from app.agent.state import AgentState
from app.collectors.windows import MetricWindowStore, TraceCache
from app.repositories.agent_runs import AgentRunCreate, AgentRunRepository
from app.repositories.alerts import AlertEventRepository

log = logging.getLogger("agent.runner")


class AgentRunSummary(BaseModel):
    run_id: int = Field(serialization_alias="runId")
    status: str
    anomaly_detected: bool = Field(serialization_alias="anomalyDetected")
    fault_service: str | None = Field(serialization_alias="faultService")
    anomaly_type: str | None = Field(serialization_alias="anomalyType")
    severity: str | None
    confidence: float | None


class AgentRunner:
    def __init__(
        self,
        *,
        session: AsyncSession,
        project_id: str,
        redis: Any,
        metric_window_store: MetricWindowStore,
        trace_cache: TraceCache,
    ) -> None:
        self.session = session
        self.project_id = project_id
        self.redis = redis
        self.metric_window_store = metric_window_store
        self.trace_cache = trace_cache
        self.repo = AgentRunRepository(session, project_id)

    async def _load_alert(self, alert_event_id: int):
        return await AlertEventRepository(self.session, self.project_id).get(
            alert_event_id
        )

    async def run_once(
        self,
        *,
        trigger_source: str = "manual",
        alert_event_id: int | None = None,
    ) -> AgentRunSummary:
        run = await self.repo.create(
            AgentRunCreate(
                trigger_source=trigger_source,
                status="running",
                alert_event_id=alert_event_id,
            )
        )
        await self.session.commit()

        try:
            initial_state = AgentState(
                project_id=self.project_id,
                trigger_source=trigger_source,
                alert_event_id=alert_event_id,
            )
            context = AgentNodeContext(
                redis=self.redis,
                metric_window_store=self.metric_window_store,
                trace_cache=self.trace_cache,
                session=self.session,
                alert_loader=self._load_alert,
            )
            graph = await _maybe_await(build_agent_graph(context))
            final_state = await graph.ainvoke(initial_state)
            state = _coerce_state(final_state)
            await self.repo.finish(
                run.id,
                status="completed",
                conclusion=_conclusion_from_state(state),
                healing_decision=_healing_decision_from_state(state),
                node_states=state.node_states,
                evidence_chain=state.evidence_chain,
                timeline=state.timeline,
                rag_results=state.rag_results,
                stats={
                    "llm_calls_count": int(state.stats.get("llm_calls_count", 0)),
                    "analyzed_logs_count": int(
                        state.stats.get("analyzed_logs_count", 0)
                    ),
                    "related_traces_count": int(
                        state.stats.get("related_traces_count", 0)
                    ),
                },
            )
            await self.session.commit()
            return _summary_from_state(run.id, "completed", state)
        except Exception:
            await self.session.rollback()
            try:
                await self.repo.finish(
                    run.id,
                    status="failed",
                    node_states={},
                    evidence_chain={},
                    timeline=[],
                    rag_results=[],
                    stats={},
                )
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                log.exception("failed to mark agent run %s as failed", run.id)
            raise


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _coerce_state(value: Any) -> AgentState:
    if isinstance(value, AgentState):
        return value
    if isinstance(value, dict):
        return AgentState.model_validate(value)
    return AgentState.model_validate(value.model_dump())


def _conclusion_from_state(state: AgentState) -> dict[str, Any]:
    if state.conclusion:
        return state.conclusion
    return {
        "fault_service": state.fault_service,
        "anomaly_type": state.anomaly_type,
        "severity": state.severity,
        "confidence": state.confidence,
        "root_cause_summary": state.root_cause_summary,
    }


def _healing_decision_from_state(state: AgentState) -> dict[str, Any]:
    if state.healing_decision:
        return state.healing_decision
    return {
        "action_type": state.action_type,
        "target_resource": state.target_resource,
        "auto_heal": state.auto_heal,
        "risk_level": state.risk_level,
    }


def _summary_from_state(
    run_id: int,
    status: str,
    state: AgentState,
) -> AgentRunSummary:
    return AgentRunSummary(
        run_id=run_id,
        status=status,
        anomaly_detected=state.anomaly_detected,
        fault_service=state.fault_service,
        anomaly_type=state.anomaly_type,
        severity=state.severity,
        confidence=state.confidence,
    )
