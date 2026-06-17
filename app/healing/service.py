"""Healing action lifecycle service."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.audit.service import AuditService
from app.alerts.state_machine import transition_alert_status
from app.healing.executor import HealingExecutor
from app.models.alert_event import AlertEvent
from app.models.heal_action import HealAction
from app.repositories.alerts import AlertEventRepository
from app.repositories.healing import HealActionCreate, HealActionRepository


@dataclass(slots=True)
class HealingDecision:
    """Agent healing decision converted into a persisted action."""

    action_type: str
    target_resource: str
    risk_level: str
    target_namespace: str | None = None
    operator: str = "aiops_agent"


class HealingService:
    """Coordinates project-scoped healing actions, alerts, and audit logs."""

    def __init__(
        self,
        repository: HealActionRepository,
        alert_repository: AlertEventRepository,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository
        self.alert_repository = alert_repository
        self.audit_service = audit_service

    async def create_from_decision(
        self,
        alert: AlertEvent,
        decision: HealingDecision,
    ) -> HealAction:
        """Create or return the action for this alert/action decision."""
        if alert.project_id != self.repository.project_id:
            raise ValueError(
                f"alert project {alert.project_id!r} does not match healing project "
                f"{self.repository.project_id!r}"
            )
        status = "waiting_approval" if decision.risk_level == "high" else "pending"
        return await self.repository.get_or_create(
            HealActionCreate(
                alert_event_id=alert.id,
                action_type=decision.action_type,
                target_resource=decision.target_resource,
                target_namespace=decision.target_namespace,
                status=status,
                risk_level=decision.risk_level,
                operator=decision.operator,
            )
        )

    async def approve(
        self,
        action_id: int,
        approver_note: str | None,
        operator_uid: str,
    ) -> HealAction:
        """Approve a high-risk waiting action and make it executable."""
        action = await self._get_action_for_update(action_id)
        self._require_status(action, {"waiting_approval"})
        before = self._snapshot(action)

        action.status = "pending"
        action.approved_by = operator_uid
        action.approver_note = approver_note
        action.updated_at = self._now()

        await self._record_audit(
            action="HEAL_APPROVE",
            heal_action=action,
            operator_uid=operator_uid,
            before_state=before,
            after_state=self._snapshot(action),
            reason=approver_note,
            result="success",
        )
        return action

    async def reject(
        self,
        action_id: int,
        reason: str,
        operator_uid: str,
    ) -> HealAction:
        """Reject a high-risk waiting action."""
        action = await self._get_action_for_update(action_id)
        self._require_status(action, {"waiting_approval"})
        before = self._snapshot(action)

        now = self._now()
        action.status = "failed"
        action.result_message = reason
        action.finished_at = now
        action.updated_at = now

        await self._record_audit(
            action="HEAL_CANCEL",
            heal_action=action,
            operator_uid=operator_uid,
            before_state=before,
            after_state=self._snapshot(action),
            reason=reason,
            result="success",
        )
        return action

    async def execute(
        self,
        action_id: int,
        executor: HealingExecutor,
        max_retries: int = 3,
    ) -> HealAction:
        """Execute a pending action, retrying failures up to max_retries times."""
        action = await self._get_action(action_id)
        self._require_status(action, {"pending"})
        before = self._snapshot(action)

        now = self._now()
        action.status = "executing"
        action.executed_at = now
        action.updated_at = now
        await self._mark_alert_healing(action)

        last_error: BaseException | None = None
        for attempt in range(1, max_retries + 2):
            try:
                action.result_message = await executor.execute(action)
                action.retry_count = attempt - 1
                action.status = "success"
                break
            except Exception as exc:  # noqa: BLE001 - executor boundary normalizes failures
                last_error = exc
                action.retry_count = attempt - 1
                action.result_message = str(exc)
        else:
            action.status = "failed"

        if action.status != "success" and last_error is not None:
            action.result_message = str(last_error)
            await self._mark_alert_failed(action)

        action.finished_at = self._now()
        action.updated_at = action.finished_at
        await self._record_audit(
            action="HEAL_EXECUTE",
            heal_action=action,
            operator_uid=None,
            before_state=before,
            after_state=self._snapshot(action),
            reason=None,
            result="success" if action.status == "success" else "failed",
        )
        return action

    async def verify(self, action_id: int, recovered: bool = True) -> HealAction:
        """Verify recovery after a successful action."""
        action = await self._get_action(action_id)
        self._require_status(action, {"success"})
        now = self._now()
        action.status = "verified" if recovered else "failed"
        action.finished_at = now
        action.updated_at = now
        if not recovered:
            action.result_message = "recovery verification failed"
            await self._mark_alert_failed(action)
            return action

        if action.alert_event_id is not None:
            alert = await self.alert_repository.get(action.alert_event_id)
            if alert is not None:
                if alert.status != "resolved":
                    alert.status = transition_alert_status(alert.status, "resolved")
                alert.resolved_at = now
                alert.updated_at = now
        return action

    async def _get_action(self, action_id: int) -> HealAction:
        action = await self.repository.get(action_id)
        if action is None:
            raise LookupError(f"heal action {action_id} not found")
        return action

    async def _get_action_for_update(self, action_id: int) -> HealAction:
        action = await self.repository.get_for_update(action_id)
        if action is None:
            raise LookupError(f"heal action {action_id} not found")
        return action

    def _require_status(self, action: HealAction, allowed: set[str]) -> None:
        if action.status not in allowed:
            allowed_text = ", ".join(sorted(allowed))
            raise ValueError(
                f"heal action {action.id} is {action.status}, expected {allowed_text}"
            )

    async def _mark_alert_healing(self, action: HealAction) -> None:
        if action.alert_event_id is None:
            return
        alert = await self.alert_repository.get(action.alert_event_id)
        if alert is None:
            return
        if alert.status == "firing":
            alert.status = transition_alert_status(alert.status, "processing")
        if alert.status == "processing":
            alert.status = transition_alert_status(alert.status, "healing")
        alert.related_heal_action_id = action.id
        alert.updated_at = self._now()

    async def _mark_alert_failed(self, action: HealAction) -> None:
        if action.alert_event_id is None:
            return
        alert = await self.alert_repository.get(action.alert_event_id)
        if alert is None:
            return
        alert.status = "failed"
        alert.related_heal_action_id = action.id
        alert.updated_at = self._now()

    async def _record_audit(
        self,
        *,
        action: str,
        heal_action: HealAction,
        operator_uid: str | None,
        before_state: dict[str, Any],
        after_state: dict[str, Any],
        reason: str | None,
        result: str,
    ) -> None:
        if self.audit_service is None:
            return
        await self.audit_service.record(
            operator_type="admin" if operator_uid is not None else "system",
            operator_name=operator_uid or "aiops",
            operator_uid=operator_uid,
            action=action,
            resource_type="heal_action",
            resource_id=str(heal_action.id),
            before_state=before_state,
            after_state=after_state,
            reason=reason,
            result=result,
        )

    def _snapshot(self, action: HealAction) -> dict[str, Any]:
        return {
            "id": action.id,
            "status": action.status,
            "retry_count": action.retry_count,
            "result_message": action.result_message,
            "approved_by": action.approved_by,
        }

    def _now(self) -> datetime:
        return datetime.now(UTC)
