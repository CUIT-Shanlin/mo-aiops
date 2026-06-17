"""Alert ingestion and state transition service."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.alerts.fingerprint import compute_fingerprint
from app.alerts.state_machine import transition_alert_status
from app.models.alert_event import AlertEvent
from app.repositories.alerts import AlertEventCreate, AlertEventRepository
from app.repositories.audit import AuditLogCreate, AuditLogRepository


_AUDIT_ACTION_BY_TARGET_STATUS = {
    "acknowledged": "ALERT_ACKNOWLEDGE",
    "suppressed": "ALERT_SUPPRESS",
    "resolved": "ALERT_RESOLVE",
}


@dataclass(slots=True)
class AlertIngest:
    """Input payload for alert ingestion."""

    name: str
    severity: str
    labels: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)
    service: str | None = None
    namespace: str | None = None
    pod: str | None = None
    agent_run_id: int | None = None
    related_heal_action_id: int | None = None
    fired_at: datetime | None = None
    last_seen_at: datetime | None = None


class _AlertRepository(Protocol):
    async def create_or_update_active(self, data: AlertEventCreate) -> AlertEvent: ...

    async def get(self, alert_id: int) -> AlertEvent | None: ...


class AlertService:
    """High-level alert operations over a project-scoped repository."""

    def __init__(
        self,
        repository: AlertEventRepository,
        audit_repository: AuditLogRepository | None = None,
    ) -> None:
        self.repository = repository
        self.audit_repository = audit_repository

    async def ingest(self, data: AlertIngest) -> AlertEvent:
        """Create or update the active alert matching this payload fingerprint."""
        fingerprint = compute_fingerprint(data.name, data.labels)
        return await self.repository.create_or_update_active(
            AlertEventCreate(
                name=data.name,
                fingerprint=fingerprint,
                severity=data.severity,
                status="firing",
                service=data.service,
                namespace=data.namespace,
                pod=data.pod,
                labels=data.labels,
                annotations=data.annotations,
                agent_run_id=data.agent_run_id,
                related_heal_action_id=data.related_heal_action_id,
                fired_at=data.fired_at,
                last_seen_at=data.last_seen_at,
            )
        )

    async def transition(
        self,
        alert_id: int,
        target_status: str,
        reason: str | None = None,
    ) -> AlertEvent:
        """Move an alert to a new status after state-machine validation."""
        alert = await self.repository.get(alert_id)
        if alert is None:
            raise LookupError(f"alert {alert_id} not found")

        before_status = alert.status
        alert.status = transition_alert_status(before_status, target_status)
        now = datetime.now(UTC)
        alert.updated_at = now
        if target_status in {"resolved", "failed"}:
            alert.resolved_at = now

        if self.audit_repository is not None:
            await self.audit_repository.create(
                AuditLogCreate(
                    operator_type="system",
                    operator_name="aiops",
                    action=_AUDIT_ACTION_BY_TARGET_STATUS.get(
                        target_status,
                        "ALERT_STATUS_TRANSITION",
                    ),
                    resource_type="alert",
                    resource_id=str(alert_id),
                    before_state={"status": before_status},
                    after_state={"status": target_status},
                    reason=reason,
                    result="success",
                )
            )

        return alert
