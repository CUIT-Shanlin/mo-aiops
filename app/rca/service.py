"""Project-scoped RCA read models and execute-fix orchestration."""
from __future__ import annotations

from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.healing.service import HealingDecision, HealingService
from app.models.agent_run import AgentRun
from app.models.alert_event import AlertEvent
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.alerts import AlertEventRepository
from app.repositories.healing import HealActionRepository


class RCAService:
    """Read RCA artifacts from AgentRun and create selected healing actions."""

    def __init__(self, session: AsyncSession, project_id: str) -> None:
        self.session = session
        self.project_id = project_id
        self.run_repository = AgentRunRepository(session, project_id)

    async def get_run(self, run_id: int) -> AgentRun | None:
        """Return a project-scoped AgentRun."""
        return await self.run_repository.get(run_id)

    async def detail(self, run: AgentRun) -> dict[str, Any]:
        alerts = await self.related_alerts(run)
        evidence = _full_evidence(run)
        return {
            "id": run.id,
            "rootCauseService": run.fault_service,
            "rootCauseType": run.root_cause_summary or run.anomaly_type,
            "anomalyType": run.anomaly_type,
            "severity": run.severity,
            "confidenceScore": _confidence_score(run.confidence),
            "relatedAlertCount": len(alerts),
            "status": run.status,
            "recommendedActions": _recommended_actions(run),
            "evidenceSummary": {
                "metrics": len(evidence["metrics"]),
                "logs": len(evidence["logs"]),
                "traces": len(evidence["traces"]),
            },
        }

    def evidences(self, run: AgentRun) -> list[dict[str, Any]]:
        evidence = _full_evidence(run)
        return [
            {"type": "metrics", "items": evidence["metrics"]},
            {"type": "logs", "items": evidence["logs"]},
            {"type": "traces", "items": evidence["traces"]},
        ]

    def timeline(self, run: AgentRun) -> list[dict[str, Any]]:
        return run.timeline or []

    def full_evidence(self, run: AgentRun) -> dict[str, list[Any]]:
        return _full_evidence(run)

    async def related_alerts(self, run: AgentRun) -> list[AlertEvent]:
        predicates = [AlertEvent.agent_run_id == run.id]
        if run.alert_event_id is not None:
            predicates.append(AlertEvent.id == run.alert_event_id)
        result = await self.session.execute(
            select(AlertEvent)
            .where(
                AlertEvent.project_id == self.project_id,
                or_(*predicates),
            )
            .order_by(
                case(
                    (AlertEvent.id == run.alert_event_id, 0),
                    else_=1,
                ),
                AlertEvent.fired_at.desc(),
                AlertEvent.id.desc(),
            )
        )
        alerts_by_id: dict[int, AlertEvent] = {}
        for alert in result.scalars().all():
            alerts_by_id.setdefault(alert.id, alert)
        return list(alerts_by_id.values())

    async def execute_fix(self, run: AgentRun, steps: list[int]) -> list[str]:
        if not steps:
            raise ValueError("至少选择一个修复步骤")
        if len(steps) != len(set(steps)):
            raise ValueError("修复步骤不能重复")

        alerts = await self.related_alerts(run)
        if not alerts:
            raise LookupError("RCA 未关联告警")
        alert = _target_alert(run, alerts)

        actions_by_step = {}
        for recommended_action in _recommended_actions(run):
            step = recommended_action.get("step")
            if step is not None:
                actions_by_step[int(step)] = recommended_action
        if not actions_by_step:
            raise ValueError("RCA 没有可执行的推荐动作")

        selected_actions: list[dict[str, Any]] = []
        action_types: set[str] = set()
        for step in steps:
            selected_action = actions_by_step.get(step)
            if selected_action is None:
                raise ValueError(f"修复步骤不存在: {step}")
            action_type, _risk_level = _map_description(selected_action)
            if action_type in action_types:
                raise ValueError("修复步骤包含重复的动作类型")
            action_types.add(action_type)
            selected_actions.append(selected_action)

        alert_repository = AlertEventRepository(self.session, self.project_id)
        healing_service = HealingService(
            HealActionRepository(self.session, self.project_id),
            alert_repository,
        )
        action_ids: list[str] = []
        for action in selected_actions:
            heal_action = await healing_service.create_from_decision(
                alert,
                _decision_from_action(run, alert, action),
            )
            action_ids.append(str(heal_action.id))
        return action_ids


def alert_to_dict(alert: AlertEvent) -> dict[str, Any]:
    """Serialize alerts with the same public shape as alerts API."""
    return {
        "id": alert.id,
        "name": alert.name,
        "severity": alert.severity,
        "status": alert.status,
        "service": alert.service,
        "namespace": alert.namespace,
        "pod": alert.pod,
        "fingerprint": alert.fingerprint,
        "labels": alert.labels,
        "annotations": alert.annotations,
        "firedAt": _format_datetime(alert.fired_at),
        "lastSeenAt": _format_datetime(alert.last_seen_at),
        "resolvedAt": _format_datetime(alert.resolved_at),
        "alertCount": alert.alert_count,
        "relatedRCA": alert.agent_run_id,
        "relatedHealing": alert.related_heal_action_id,
    }


def _recommended_actions(run: AgentRun) -> list[dict[str, Any]]:
    for container in (run.node_states or {}, run.evidence_chain or {}):
        actions = container.get("recommended_actions")
        if isinstance(actions, list):
            return [action for action in actions if isinstance(action, dict)]

    if run.action_type and run.target_resource:
        return [
            {
                "step": 1,
                "description": f"{run.action_type} {run.target_resource}",
                "actionType": run.action_type,
                "targetResource": run.target_resource,
                "riskLevel": run.risk_level or "high",
            }
        ]
    return []


def _target_alert(run: AgentRun, alerts: list[AlertEvent]) -> AlertEvent:
    if run.alert_event_id is not None:
        for alert in alerts:
            if alert.id == run.alert_event_id:
                return alert
    if len(alerts) == 1:
        return alerts[0]
    raise ValueError("RCA 关联多个告警，请先指定主告警")


def _map_description(action: dict[str, Any]) -> tuple[str, str]:
    description = str(action.get("description") or "")
    lower_description = description.lower()
    if "hpa" in lower_description:
        return "hpa_scale", "medium"
    elif "重启" in description or "restart" in lower_description:
        return "pod_restart", "medium"
    return "rolling_restart", "high"


def _decision_from_action(
    run: AgentRun,
    alert: AlertEvent,
    action: dict[str, Any],
) -> HealingDecision:
    action_type, risk_level = _map_description(action)
    explicit_target = action.get("targetResource") or action.get("target_resource")
    if action_type == "pod_restart":
        target_resource = (
            explicit_target
            or alert.pod
            or run.target_resource
            or run.fault_service
            or "unknown"
        )
    else:
        target_resource = (
            explicit_target
            or run.target_resource
            or run.fault_service
            or alert.service
            or "unknown"
        )

    return HealingDecision(
        action_type=action_type,
        target_resource=str(target_resource),
        target_namespace=(
            action.get("targetNamespace")
            or action.get("target_namespace")
            or alert.namespace
        ),
        risk_level=risk_level,
        operator="aiops_agent",
    )


def _full_evidence(run: AgentRun) -> dict[str, list[Any]]:
    chain = run.evidence_chain or {}
    return {
        "metrics": _as_list(chain.get("metrics")),
        "logs": _as_list(chain.get("logs")),
        "traces": _as_list(chain.get("traces")),
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _confidence_score(value: float | None) -> int | None:
    if value is None:
        return None
    return int(value * 100) if value <= 1 else int(value)


def _format_datetime(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")
