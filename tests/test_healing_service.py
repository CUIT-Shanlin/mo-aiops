from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import text

from app.audit.service import AuditService
from app.healing.executor import HealingExecutor
from app.healing.service import HealingDecision, HealingService
from app.models.heal_action import HealAction
from app.repositories.alerts import AlertEventCreate, AlertEventRepository
from app.repositories.audit import AuditLogRepository
from app.repositories.healing import HealActionRepository


@pytest.fixture(autouse=True)
async def _clean_m4_tables(app_instance):
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "TRUNCATE notifications, audit_logs, heal_actions, alert_events "
                "RESTART IDENTITY"
            )
        )
        await session.commit()
    yield
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "TRUNCATE notifications, audit_logs, heal_actions, alert_events "
                "RESTART IDENTITY"
            )
        )
        await session.commit()


class FlakyExecutor:
    def __init__(self, outcomes: Sequence[BaseException | str]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def execute(self, action: HealAction) -> str:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _service(
    session,
    project_id: str = "prod",
) -> HealingService:
    return HealingService(
        HealActionRepository(session, project_id),
        AlertEventRepository(session, project_id),
        AuditService(AuditLogRepository(session, project_id)),
    )


async def _create_alert(session, project_id: str = "prod", status: str = "processing"):
    return await AlertEventRepository(session, project_id).create(
        AlertEventCreate(
            name="HighCPU",
            fingerprint=f"fp-{project_id}-{status}",
            severity="critical",
            status=status,
            service="message-service",
        )
    )


async def test_create_from_decision_sets_status_by_risk_level(app_instance):
    async with app_instance.state.sessionmaker() as session:
        alert = await _create_alert(session)
        service = _service(session)

        high = await service.create_from_decision(
            alert,
            HealingDecision(
                action_type="rolling_restart",
                target_resource="deployment/message-service",
                risk_level="high",
            ),
        )
        medium = await service.create_from_decision(
            alert,
            HealingDecision(
                action_type="hpa_scale",
                target_resource="deployment/message-service",
                risk_level="medium",
            ),
        )
        await session.commit()

    assert high.status == "waiting_approval"
    assert medium.status == "pending"
    assert high.project_id == "prod"
    assert medium.project_id == "prod"


async def test_create_from_decision_returns_existing_same_project_alert_action(
    app_instance,
):
    async with app_instance.state.sessionmaker() as session:
        alert = await _create_alert(session)
        service = _service(session)
        decision = HealingDecision(
            action_type="pod_restart",
            target_resource="pod/message-0",
            risk_level="low",
        )

        first = await service.create_from_decision(alert, decision)
        second = await service.create_from_decision(alert, decision)
        await session.commit()

    assert first.id == second.id
    assert first.status == "pending"


async def test_approve_moves_waiting_action_to_pending_and_writes_audit(app_instance):
    async with app_instance.state.sessionmaker() as session:
        alert = await _create_alert(session)
        service = _service(session)
        action = await service.create_from_decision(
            alert,
            HealingDecision(
                action_type="node_evict",
                target_resource="node/node-1",
                risk_level="high",
            ),
        )

        approved = await service.approve(
            action.id,
            approver_note="approved in incident bridge",
            operator_uid="admin-1",
        )
        await session.commit()

        audits = await AuditLogRepository(session, "prod").list(page=1, page_size=20)

    assert approved.status == "pending"
    assert approved.approved_by == "admin-1"
    assert approved.approver_note == "approved in incident bridge"
    assert audits.total == 1
    assert audits.items[0].action == "HEAL_APPROVE"
    assert audits.items[0].resource_id == str(action.id)


async def test_reject_moves_waiting_action_to_failed_and_writes_audit(app_instance):
    async with app_instance.state.sessionmaker() as session:
        alert = await _create_alert(session)
        service = _service(session)
        action = await service.create_from_decision(
            alert,
            HealingDecision(
                action_type="node_evict",
                target_resource="node/node-1",
                risk_level="high",
            ),
        )

        rejected = await service.reject(
            action.id,
            reason="too risky during peak traffic",
            operator_uid="admin-1",
        )
        await session.commit()

        audits = await AuditLogRepository(session, "prod").list(page=1, page_size=20)

    assert rejected.status == "failed"
    assert rejected.result_message == "too risky during peak traffic"
    assert rejected.finished_at is not None
    assert audits.total == 1
    assert audits.items[0].action == "HEAL_CANCEL"
    assert audits.items[0].reason == "too risky during peak traffic"


async def test_approve_and_reject_same_action_serializes_state_change(app_instance):
    async with app_instance.state.sessionmaker() as setup_session:
        alert = await _create_alert(setup_session)
        action = await _service(setup_session).create_from_decision(
            alert,
            HealingDecision(
                action_type="node_evict",
                target_resource="node/node-1",
                risk_level="high",
            ),
        )
        await setup_session.commit()
        action_id = action.id

    first_session = app_instance.state.sessionmaker()
    second_session = app_instance.state.sessionmaker()
    try:
        first_service = _service(first_session)
        second_service = _service(second_session)
        first_action = await first_service._get_action(action_id)
        second_action = await second_service._get_action(action_id)
        assert first_action.status == "waiting_approval"
        assert second_action.status == "waiting_approval"

        approved = await first_service.approve(
            action_id,
            approver_note="approved first",
            operator_uid="admin-1",
        )
        await first_session.commit()

        with pytest.raises(ValueError):
            await second_service.reject(
                action_id,
                reason="late reject",
                operator_uid="admin-2",
            )
        await second_session.rollback()
    finally:
        await first_session.close()
        await second_session.close()

    async with app_instance.state.sessionmaker() as check_session:
        stored_action = await HealActionRepository(check_session, "prod").get(action_id)
        audits = await AuditLogRepository(check_session, "prod").list(
            page=1,
            page_size=20,
        )

    assert approved.status == "pending"
    assert stored_action is not None
    assert stored_action.status == "pending"
    assert audits.total == 1
    assert audits.items[0].action == "HEAL_APPROVE"


async def test_execute_retries_until_success_and_writes_audit(app_instance):
    async with app_instance.state.sessionmaker() as session:
        alert = await _create_alert(session, status="processing")
        service = _service(session)
        action = await service.create_from_decision(
            alert,
            HealingDecision(
                action_type="hpa_scale",
                target_resource="deployment/message-service",
                risk_level="medium",
            ),
        )
        executor: HealingExecutor = FlakyExecutor(
            [
                RuntimeError("api timeout"),
                RuntimeError("conflict"),
                RuntimeError("stale resource"),
                "scaled to 4",
            ]
        )

        executed = await service.execute(action.id, executor, max_retries=3)
        await session.commit()

        audits = await AuditLogRepository(session, "prod").list(page=1, page_size=20)
        stored_alert = await AlertEventRepository(session, "prod").get(alert.id)

    assert executed.status == "success"
    assert executed.retry_count == 3
    assert executed.result_message == "scaled to 4"
    assert executed.executed_at is not None
    assert executed.finished_at is not None
    assert stored_alert is not None
    assert stored_alert.status == "healing"
    assert audits.total == 1
    assert audits.items[0].action == "HEAL_EXECUTE"
    assert audits.items[0].result == "success"


async def test_execute_marks_failed_after_max_retries(app_instance):
    async with app_instance.state.sessionmaker() as session:
        alert = await _create_alert(session)
        service = _service(session)
        action = await service.create_from_decision(
            alert,
            HealingDecision(
                action_type="pod_restart",
                target_resource="pod/message-0",
                risk_level="low",
            ),
        )
        executor: HealingExecutor = FlakyExecutor(
            [RuntimeError("first"), RuntimeError("second"), RuntimeError("third")]
        )

        executed = await service.execute(action.id, executor, max_retries=2)
        await session.commit()

        audits = await AuditLogRepository(session, "prod").list(page=1, page_size=20)
        stored_alert = await AlertEventRepository(session, "prod").get(alert.id)

    assert executed.status == "failed"
    assert executed.retry_count == 2
    assert executed.result_message == "third"
    assert executed.finished_at is not None
    assert stored_alert is not None
    assert stored_alert.status == "failed"
    assert audits.total == 1
    assert audits.items[0].action == "HEAL_EXECUTE"
    assert audits.items[0].result == "failed"


async def test_verify_success_marks_action_verified_and_alert_resolved(app_instance):
    async with app_instance.state.sessionmaker() as session:
        alert = await _create_alert(session, status="processing")
        service = _service(session)
        action = await service.create_from_decision(
            alert,
            HealingDecision(
                action_type="hpa_scale",
                target_resource="deployment/message-service",
                risk_level="medium",
            ),
        )
        await service.execute(action.id, FlakyExecutor(["scaled to 4"]))

        verified = await service.verify(action.id, recovered=True)
        await session.commit()

        stored_alert = await AlertEventRepository(session, "prod").get(alert.id)

    assert verified.status == "verified"
    assert stored_alert is not None
    assert stored_alert.status == "resolved"
    assert stored_alert.resolved_at is not None


async def test_verify_failure_marks_action_and_alert_failed(app_instance):
    async with app_instance.state.sessionmaker() as session:
        alert = await _create_alert(session, status="processing")
        service = _service(session)
        action = await service.create_from_decision(
            alert,
            HealingDecision(
                action_type="hpa_scale",
                target_resource="deployment/message-service",
                risk_level="medium",
            ),
        )
        await service.execute(action.id, FlakyExecutor(["scaled to 4"]))

        verified = await service.verify(action.id, recovered=False)
        await session.commit()

        stored_alert = await AlertEventRepository(session, "prod").get(alert.id)

    assert verified.status == "failed"
    assert verified.result_message == "recovery verification failed"
    assert stored_alert is not None
    assert stored_alert.status == "failed"


async def test_healing_service_is_project_scoped(app_instance):
    async with app_instance.state.sessionmaker() as session:
        prod_alert = await _create_alert(session, "prod")
        stage_alert = await _create_alert(session, "stage")
        prod_action = await _service(session, "prod").create_from_decision(
            prod_alert,
            HealingDecision(
                action_type="pod_restart",
                target_resource="pod/message-0",
                risk_level="low",
            ),
        )
        await _service(session, "stage").create_from_decision(
            stage_alert,
            HealingDecision(
                action_type="pod_restart",
                target_resource="pod/message-0",
                risk_level="low",
            ),
        )

        with pytest.raises(LookupError):
            await _service(session, "stage").execute(
                prod_action.id,
                FlakyExecutor(["should not run"]),
            )


async def test_create_from_decision_rejects_cross_project_alert(app_instance):
    async with app_instance.state.sessionmaker() as session:
        stage_alert = await _create_alert(session, "stage")

        with pytest.raises(ValueError, match="project"):
            await _service(session, "prod").create_from_decision(
                stage_alert,
                HealingDecision(
                    action_type="pod_restart",
                    target_resource="pod/message-0",
                    risk_level="low",
                ),
            )
