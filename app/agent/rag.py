"""Deterministic similar-case retrieval for Agent RAG."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.repositories.agent_runs import AgentRunRepository


class SimilarCaseQuery(BaseModel):
    """Query parameters for similar historical Agent cases."""

    alert_type: str = Field(min_length=1)
    service: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1)


class SimilarCase(BaseModel):
    """API shape returned to the AIOps console."""

    caseId: str
    title: str
    similarity: float
    rootCause: str
    resolution: str
    resolvedAt: str | None = None


def _tokens(*values: str | None) -> set[str]:
    text = " ".join(value or "" for value in values).lower()
    return {token for token in re.split(r"[^a-z0-9]+", text) if token}


def score_case(*, alert_type: str, service: str, run: dict[str, Any]) -> float:
    """Score a historical run against a query using deterministic signals."""

    score = 0.0
    if (run.get("fault_service") or "").lower() == service.lower():
        score += 0.45
    if (run.get("anomaly_type") or "").lower() == alert_type.lower():
        score += 0.35

    query_tokens = _tokens(alert_type, service)
    case_tokens = _tokens(
        run.get("fault_service"),
        run.get("anomaly_type"),
        run.get("severity"),
        run.get("root_cause_summary"),
    )
    if query_tokens and case_tokens:
        score += 0.20 * (len(query_tokens & case_tokens) / len(query_tokens | case_tokens))

    return min(round(score, 4), 1.0)


class SimilarCaseService:
    """Project-scoped similar-case lookup over completed Agent runs."""

    def __init__(self, session: AsyncSession, project_id: str) -> None:
        self.repo = AgentRunRepository(session, project_id)

    async def find_similar_cases(self, query: SimilarCaseQuery) -> list[SimilarCase]:
        runs = await self.repo.list_completed(limit=100)
        ranked: list[tuple[float, datetime, int, AgentRun]] = []

        for run in runs:
            similarity = score_case(
                alert_type=query.alert_type,
                service=query.service,
                run={
                    "fault_service": run.fault_service,
                    "anomaly_type": run.anomaly_type,
                    "severity": run.severity,
                    "root_cause_summary": run.root_cause_summary,
                },
            )
            if similarity > 0:
                ranked.append((similarity, self._sort_time(run), run.id, run))

        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [
            self._to_case(run, similarity)
            for similarity, _, _, run in ranked[: query.limit]
        ]

    def _sort_time(self, run: AgentRun) -> datetime:
        return run.finished_at or run.created_at or datetime.min.replace(tzinfo=UTC)

    def _to_case(self, run: AgentRun, similarity: float) -> SimilarCase:
        resolved_at = run.finished_at or run.created_at
        return SimilarCase(
            caseId=f"case-{run.id}",
            title=f"{run.fault_service or 'unknown-service'} {run.anomaly_type or 'UnknownAnomaly'}",
            similarity=round(similarity, 2),
            rootCause=run.root_cause_summary or "",
            resolution="参考历史 RCA 结论与处置记录",
            resolvedAt=self._format_resolved_at(resolved_at),
        )

    def _format_resolved_at(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
