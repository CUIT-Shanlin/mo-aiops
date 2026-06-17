import asyncio

import pytest
from sqlalchemy import text

from app.alerts.service import AlertIngest, AlertService
from app.repositories.alerts import AlertEventCreate, AlertEventRepository
from app.repositories.audit import AuditLogCreate, AuditLogRepository
from app.repositories.healing import HealActionCreate, HealActionRepository
from app.repositories.notifications import NotificationCreate, NotificationRepository


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


async def test_m4_repositories_are_project_scoped(app_instance):
    async with app_instance.state.sessionmaker() as session:
        prod_alerts = AlertEventRepository(session, "prod")
        stage_alerts = AlertEventRepository(session, "stage")
        prod = await prod_alerts.create(
            AlertEventCreate(
                name="PodCrashLoopBackOff",
                fingerprint="fp1",
                severity="critical",
                status="firing",
                labels={"service": "message-service"},
            )
        )
        await stage_alerts.create(
            AlertEventCreate(
                name="PodCrashLoopBackOff",
                fingerprint="fp1",
                severity="critical",
                status="firing",
                labels={"service": "stage-service"},
            )
        )
        await session.commit()

        prod_rows = await AlertEventRepository(session, "prod").list(
            page=1, page_size=20
        )
        stage_rows = await AlertEventRepository(session, "stage").list(
            page=1, page_size=20
        )

    assert [row.project_id for row in prod_rows.items] == ["prod"]
    assert [row.project_id for row in stage_rows.items] == ["stage"]
    assert prod_rows.items[0].labels["service"] == "message-service"
    assert prod.id != stage_rows.items[0].id


async def test_heal_action_unique_per_project_alert_action(app_instance):
    async with app_instance.state.sessionmaker() as session:
        alerts = AlertEventRepository(session, "prod")
        alert = await alerts.create(
            AlertEventCreate(
                name="HighCPU",
                fingerprint="fp-cpu",
                severity="warning",
                status="healing",
            )
        )
        healing = HealActionRepository(session, "prod")
        first = await healing.create(
            HealActionCreate(
                alert_event_id=alert.id,
                action_type="hpa_scale",
                target_resource="message-service",
                status="pending",
                risk_level="medium",
                operator="aiops_agent",
            )
        )
        second = await healing.get_or_create(
            HealActionCreate(
                alert_event_id=alert.id,
                action_type="hpa_scale",
                target_resource="message-service",
                status="pending",
                risk_level="medium",
                operator="aiops_agent",
            )
        )
        await session.commit()

    assert first.id == second.id


async def test_alert_repository_updates_active_duplicate_without_insert(app_instance):
    async with app_instance.state.sessionmaker() as session:
        repo = AlertEventRepository(session, "prod")
        first = await repo.create_or_update_active(
            AlertEventCreate(
                name="HighCPU",
                fingerprint="fp-dup",
                severity="warning",
                status="firing",
                labels={"service": "message-service"},
            )
        )
        second = await repo.create_or_update_active(
            AlertEventCreate(
                name="HighCPU",
                fingerprint="fp-dup",
                severity="critical",
                status="firing",
                labels={"service": "message-service"},
            )
        )
        await session.commit()

        rows = await AlertEventRepository(session, "prod").list(page=1, page_size=20)

    assert first.id == second.id
    assert rows.total == 1
    assert rows.items[0].severity == "critical"
    assert rows.items[0].alert_count == 2


async def test_alert_repository_concurrent_active_duplicate_is_upserted(app_instance):
    async def ingest_once(severity: str) -> int:
        async with app_instance.state.sessionmaker() as session:
            alert = await AlertEventRepository(session, "prod").create_or_update_active(
                AlertEventCreate(
                    name="HighCPU",
                    fingerprint="fp-concurrent",
                    severity=severity,
                    status="firing",
                    labels={"service": "message-service"},
                )
            )
            await session.commit()
            return alert.id

    ids = await asyncio.gather(ingest_once("warning"), ingest_once("critical"))

    async with app_instance.state.sessionmaker() as session:
        rows = await AlertEventRepository(session, "prod").list(page=1, page_size=20)

    assert ids[0] == ids[1]
    assert rows.total == 1
    assert rows.items[0].alert_count == 2


async def test_alert_service_ingest_computes_fingerprint_and_deduplicates_active_alert(
    app_instance,
):
    async with app_instance.state.sessionmaker() as session:
        service = AlertService(AlertEventRepository(session, "prod"))
        first = await service.ingest(
            AlertIngest(
                name="HighCPU",
                severity="warning",
                labels={"service": "message-service", "pod": "message-0"},
            )
        )
        second = await service.ingest(
            AlertIngest(
                name="HighCPU",
                severity="critical",
                labels={"pod": "message-0", "service": "message-service"},
            )
        )
        await session.commit()

        rows = await AlertEventRepository(session, "prod").list(page=1, page_size=20)

    assert first.id == second.id
    assert rows.total == 1
    assert rows.items[0].fingerprint == first.fingerprint
    assert rows.items[0].severity == "critical"
    assert rows.items[0].alert_count == 2


async def test_alert_service_ingest_reopens_after_resolved(app_instance):
    async with app_instance.state.sessionmaker() as session:
        repo = AlertEventRepository(session, "prod")
        service = AlertService(repo)
        first = await service.ingest(
            AlertIngest(
                name="HighCPU",
                severity="warning",
                labels={"service": "message-service"},
            )
        )
        resolved = await service.transition(first.id, "resolved", reason="recovered")
        reopened = await service.ingest(
            AlertIngest(
                name="HighCPU",
                severity="critical",
                labels={"service": "message-service"},
            )
        )
        await session.commit()

        rows = await AlertEventRepository(session, "prod").list(page=1, page_size=20)

    assert resolved.status == "resolved"
    assert resolved.resolved_at is not None
    assert reopened.id != first.id
    assert reopened.fingerprint == first.fingerprint
    assert rows.total == 2


@pytest.mark.parametrize(
    ("target_status", "expected_action"),
    [
        ("acknowledged", "ALERT_ACKNOWLEDGE"),
        ("suppressed", "ALERT_SUPPRESS"),
        ("resolved", "ALERT_RESOLVE"),
    ],
)
async def test_alert_service_transition_writes_required_audit_action(
    app_instance,
    target_status,
    expected_action,
):
    async with app_instance.state.sessionmaker() as session:
        alert_repo = AlertEventRepository(session, "prod")
        audit_repo = AuditLogRepository(session, "prod")
        service = AlertService(alert_repo, audit_repo)
        alert = await service.ingest(
            AlertIngest(
                name=f"HighCPU-{target_status}",
                severity="warning",
                labels={"service": f"message-{target_status}"},
            )
        )

        await service.transition(alert.id, target_status, reason="manual")
        await session.commit()

        audit_rows = await AuditLogRepository(session, "prod").list(
            page=1,
            page_size=20,
        )

    assert audit_rows.total == 1
    assert audit_rows.items[0].action == expected_action
    assert audit_rows.items[0].resource_id == str(alert.id)


async def test_alert_service_keeps_project_isolation_for_same_fingerprint(app_instance):
    payload = AlertIngest(
        name="HighCPU",
        severity="warning",
        labels={"service": "message-service"},
    )
    async with app_instance.state.sessionmaker() as session:
        prod = await AlertService(AlertEventRepository(session, "prod")).ingest(payload)
        stage = await AlertService(AlertEventRepository(session, "stage")).ingest(payload)
        await session.commit()

        prod_rows = await AlertEventRepository(session, "prod").list(page=1, page_size=20)
        stage_rows = await AlertEventRepository(session, "stage").list(
            page=1, page_size=20
        )

    assert prod.fingerprint == stage.fingerprint
    assert prod.id != stage.id
    assert [row.project_id for row in prod_rows.items] == ["prod"]
    assert [row.project_id for row in stage_rows.items] == ["stage"]


async def test_audit_logs_allow_null_project_and_notifications_are_project_scoped(
    app_instance,
):
    async with app_instance.state.sessionmaker() as session:
        global_audit = await AuditLogRepository(session, None).create(
            AuditLogCreate(
                operator_type="admin",
                operator_name="admin",
                action="USER_BAN",
                resource_type="user",
                resource_id="u1",
                result="success",
            )
        )
        await NotificationRepository(session, "prod").create(
            NotificationCreate(type="alert", title="New alert", message="prod")
        )
        await NotificationRepository(session, "stage").create(
            NotificationCreate(type="alert", title="New alert", message="stage")
        )
        await AuditLogRepository(session, "prod").create(
            AuditLogCreate(
                operator_type="admin",
                operator_name="admin",
                action="ALERT_SUPPRESS",
                resource_type="alert",
                resource_id="a1",
                result="success",
            )
        )
        await session.commit()

        prod_notifications = await NotificationRepository(session, "prod").list(
            page=1, page_size=20
        )
        global_row = await AuditLogRepository(session, None).get(global_audit.id)
        global_rows = await AuditLogRepository(session, None).list(page=1, page_size=20)

    assert global_row is not None
    assert global_row.project_id is None
    assert [row.project_id for row in global_rows.items] == [None]
    assert [item.message for item in prod_notifications.items] == ["prod"]
