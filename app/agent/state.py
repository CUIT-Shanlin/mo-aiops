from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """LangGraph agent state passed between deterministic Agent nodes."""

    project_id: str
    trigger_source: str
    alert_event_id: int | None = None

    metrics_summary: str = ""
    logs_summary: str = ""
    traces_summary: str = ""
    summaries: dict[str, Any] = Field(default_factory=dict)

    metrics_evidence: list[dict[str, Any]] = Field(default_factory=list)
    logs_evidence: list[dict[str, Any]] = Field(default_factory=list)
    traces_evidence: list[dict[str, Any]] = Field(default_factory=list)
    evidence_chain: dict[str, Any] = Field(default_factory=dict)

    anomaly_detected: bool = False
    fault_service: str | None = None
    anomaly_type: str | None = None
    severity: Literal["info", "warning", "critical"] | None = None
    confidence: float | None = None
    root_cause_summary: str | None = None
    conclusion: dict[str, Any] = Field(default_factory=dict)

    action_type: str | None = None
    target_resource: str | None = None
    auto_heal: bool = False
    risk_level: Literal["none", "low", "medium", "high"] | None = None
    healing_decision: dict[str, Any] = Field(default_factory=dict)

    node_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    rag_results: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
