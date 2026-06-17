from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.core.constants import RedisKey
from app.core.projects import ProjectConfig, ProjectsConfig
from app.models.audit_log import AuditLog
from app.repositories.agent_runs import AgentRunCreate, AgentRunRepository
from app.repositories.alerts import AlertEventCreate, AlertEventRepository
from app.repositories.audit import AuditLogRepository


@pytest.fixture(autouse=True)
async def _clean_alerts_api_tables(app_instance):
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "TRUNCATE notifications, audit_logs, heal_actions, agent_runs, alert_events "
                "RESTART IDENTITY"
            )
        )
        await session.commit()
    yield
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "TRUNCATE notifications, audit_logs, heal_actions, agent_runs, alert_events "
                "RESTART IDENTITY"
            )
        )
        await session.commit()


@pytest.fixture
def alerts_projects_config(app_instance):
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
        },
    )
    return app_instance.state.projects_config


@pytest.fixture
def auth_headers(make_jwt):
    return {
        "Authorization": f"Bearer {make_jwt(role='admin')}",
        "X-Project-Id": "prod",
    }


async def _seed_alert(
    app_instance,
    *,
    project_id: str = "prod",
    name: str = "HighCPU",
    fingerprint: str = "fp-high-cpu",
    severity: str = "warning",
    status: str = "firing",
    service: str | None = "message-service",
    namespace: str | None = "prod-ns",
    pod: str | None = "message-0",
    labels: dict | None = None,
    annotations: dict | None = None,
    alert_count: int = 1,
    agent_run_id: int | None = None,
    related_heal_action_id: int | None = None,
    fired_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    resolved_at: datetime | None = None,
):
    async with app_instance.state.sessionmaker() as session:
        alert = await AlertEventRepository(session, project_id).create(
            AlertEventCreate(
                name=name,
                fingerprint=fingerprint,
                severity=severity,
                status=status,
                service=service,
                namespace=namespace,
                pod=pod,
                labels=labels or {"service": service, "namespace": namespace},
                annotations=annotations or {"summary": f"{name} summary"},
                alert_count=alert_count,
                agent_run_id=agent_run_id,
                related_heal_action_id=related_heal_action_id,
                fired_at=fired_at,
                last_seen_at=last_seen_at or fired_at,
                resolved_at=resolved_at,
            )
        )
        await session.commit()
        return alert.id


async def test_alerts_api_requires_admin_jwt(client):
    response = await client.get("/api/v1/alerts/stats", headers={"X-Project-Id": "prod"})

    assert response.status_code == 200
    assert response.json()["code"] == 40001


async def test_alerts_api_requires_known_enabled_project(client, make_jwt):
    response = await client.get(
        "/api/v1/alerts",
        headers={"Authorization": f"Bearer {make_jwt(role='admin')}"},
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "项目不存在", "data": None}


async def test_alerts_list_filters_and_returns_frontend_shape(
    app_instance,
    client,
    auth_headers,
    alerts_projects_config,
):
    now = datetime(2026, 6, 17, 9, 0, tzinfo=UTC)
    expected_id = await _seed_alert(
        app_instance,
        name="HighCPU",
        fingerprint="fp-target",
        severity="critical",
        service="message-service",
        namespace="prod-ns",
        pod="message-0",
        labels={"service": "message-service", "pod": "message-0"},
        annotations={"summary": "cpu is high"},
        alert_count=3,
        agent_run_id=11,
        related_heal_action_id=22,
        fired_at=now,
    )
    await _seed_alert(
        app_instance,
        name="HighMemory",
        fingerprint="fp-filtered",
        severity="warning",
        service="user-service",
        namespace="prod-ns",
        fired_at=now - timedelta(minutes=1),
    )
    await _seed_alert(
        app_instance,
        project_id="stage",
        name="HighCPU",
        fingerprint="fp-stage",
        severity="critical",
        service="message-service",
        namespace="prod-ns",
        fired_at=now - timedelta(minutes=2),
    )

    response = await client.get(
        "/api/v1/alerts"
        "?status=firing&severity=critical&service=message-service"
        "&namespace=prod-ns&keyword=cpu&page=1&pageSize=10",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0
    page = response.json()["data"]
    assert page["total"] == 1
    assert page["page"] == 1
    assert page["pageSize"] == 10
    assert page["items"] == [
        {
            "id": expected_id,
            "name": "HighCPU",
            "severity": "critical",
            "status": "firing",
            "service": "message-service",
            "namespace": "prod-ns",
            "pod": "message-0",
            "fingerprint": "fp-target",
            "labels": {"service": "message-service", "pod": "message-0"},
            "annotations": {"summary": "cpu is high"},
            "firedAt": "2026-06-17T09:00:00Z",
            "lastSeenAt": "2026-06-17T09:00:00Z",
            "resolvedAt": None,
            "duration": "0分钟",
            "alertCount": 3,
            "relatedRCA": 11,
            "relatedHealing": 22,
        }
    ]


async def test_alert_stats_counts_by_status_and_severity(
    app_instance,
    client,
    auth_headers,
    alerts_projects_config,
):
    await _seed_alert(app_instance, fingerprint="fp1", severity="critical", status="firing")
    await _seed_alert(app_instance, fingerprint="fp2", severity="warning", status="firing")
    await _seed_alert(app_instance, fingerprint="fp3", severity="critical", status="resolved")
    await _seed_alert(
        app_instance,
        project_id="stage",
        fingerprint="fp-stage",
        severity="critical",
        status="firing",
    )

    response = await client.get("/api/v1/alerts/stats", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {
            "total": 3,
            "critical": 2,
            "resolved": 1,
            "dedupRate": 0.0,
            "active": 2,
            "byStatus": {"firing": 2, "resolved": 1},
            "bySeverity": {"critical": 2, "warning": 1},
        },
    }


async def test_alert_stats_dedup_rate_uses_total_received_count(
    app_instance,
    client,
    auth_headers,
    alerts_projects_config,
):
    await _seed_alert(
        app_instance,
        fingerprint="fp-dedup",
        severity="critical",
        status="firing",
        alert_count=3,
    )
    await _seed_alert(
        app_instance,
        fingerprint="fp-single",
        severity="warning",
        status="firing",
        alert_count=1,
    )

    response = await client.get("/api/v1/alerts/stats", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["data"]["dedupRate"] == 50.0


async def test_acknowledge_alert_transitions_and_writes_audit(
    app_instance,
    client,
    auth_headers,
    alerts_projects_config,
):
    alert_id = await _seed_alert(app_instance, status="firing")

    response = await client.put(
        f"/api/v1/alerts/{alert_id}/acknowledge",
        json={"reason": "handled by operator"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"code": 0, "message": "success", "data": {"success": True}}
    async with app_instance.state.sessionmaker() as session:
        alert = await AlertEventRepository(session, "prod").get(alert_id)
        audits = await AuditLogRepository(session, "prod").list(page=1, page_size=20)

    assert alert is not None
    assert alert.status == "acknowledged"
    assert audits.total == 1
    assert audits.items[0].action == "ALERT_ACKNOWLEDGE"
    assert audits.items[0].operator_type == "user"
    assert audits.items[0].operator_uid == "u1"
    assert audits.items[0].reason == "handled by operator"


async def test_invalid_alert_transition_returns_validation_error_without_audit(
    app_instance,
    client,
    auth_headers,
    alerts_projects_config,
):
    alert_id = await _seed_alert(app_instance, status="suppressed")

    response = await client.put(
        f"/api/v1/alerts/{alert_id}/suppress",
        json={"duration": 0, "reason": "repeat suppress"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40022, "message": "告警状态转换无效", "data": None}
    async with app_instance.state.sessionmaker() as session:
        audits = await AuditLogRepository(session, "prod").list(page=1, page_size=20)

    assert audits.total == 0


async def test_batch_suppress_transitions_each_alert_and_writes_each_audit(
    app_instance,
    client,
    auth_headers,
    alerts_projects_config,
):
    first_id = await _seed_alert(app_instance, fingerprint="fp1", status="firing")
    second_id = await _seed_alert(app_instance, fingerprint="fp2", status="acknowledged")

    response = await client.post(
        "/api/v1/alerts/batch-suppress",
        json={"ids": [first_id, second_id], "duration": 3600, "reason": "maintenance window"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {"success": True, "count": 2},
    }
    async with app_instance.state.sessionmaker() as session:
        first = await AlertEventRepository(session, "prod").get(first_id)
        second = await AlertEventRepository(session, "prod").get(second_id)
        audit_actions = (
            await session.execute(
                select(
                    AuditLog.action,
                    AuditLog.resource_id,
                    AuditLog.reason,
                    AuditLog.after_state,
                )
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id)
            )
        ).all()

    assert first is not None
    assert second is not None
    assert first.status == "suppressed"
    assert second.status == "suppressed"
    assert audit_actions == [
        (
            "ALERT_SUPPRESS",
            str(first_id),
            "maintenance window",
            {"duration": 3600, "status": "suppressed"},
        ),
        (
            "ALERT_SUPPRESS",
            str(second_id),
            "maintenance window",
            {"duration": 3600, "status": "suppressed"},
        ),
    ]


async def test_suppress_alert_accepts_duration_and_records_it_in_audit(
    app_instance,
    client,
    auth_headers,
    alerts_projects_config,
):
    alert_id = await _seed_alert(app_instance, status="firing")

    response = await client.put(
        f"/api/v1/alerts/{alert_id}/suppress",
        json={"duration": 7200, "reason": "known maintenance"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"code": 0, "message": "success", "data": {"success": True}}
    async with app_instance.state.sessionmaker() as session:
        audit = (await AuditLogRepository(session, "prod").list(page=1, page_size=20)).items[
            0
        ]

    assert audit.action == "ALERT_SUPPRESS"
    assert audit.after_state == {"duration": 7200, "status": "suppressed"}


async def test_alert_groups_groups_active_alerts_by_service_and_name(
    app_instance,
    client,
    auth_headers,
    alerts_projects_config,
):
    now = datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
    first_id = await _seed_alert(
        app_instance,
        name="HighCPU",
        fingerprint="fp1",
        severity="warning",
        status="firing",
        service="message-service",
        fired_at=now,
    )
    second_id = await _seed_alert(
        app_instance,
        name="HighCPU",
        fingerprint="fp2",
        severity="critical",
        status="processing",
        service="message-service",
        fired_at=now + timedelta(minutes=1),
    )
    await _seed_alert(
        app_instance,
        name="HighCPU",
        fingerprint="fp-resolved",
        severity="critical",
        status="resolved",
        service="message-service",
    )

    response = await client.get("/api/v1/alert-groups", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": [
            {
                "id": "message-service:HighCPU",
                "service": "message-service",
                "name": "HighCPU",
                "severity": "critical",
                "status": "active",
                "alertCount": 2,
                "alertIds": [first_id, second_id],
                "firstFiredAt": "2026-06-17T10:00:00Z",
                "lastFiredAt": "2026-06-17T10:01:00Z",
            }
        ],
    }


async def test_alerts_project_header_rejects_disabled_project(
    app_instance,
    client,
    make_jwt,
):
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "disabled": ProjectConfig(
                name="Disabled",
                metric_profile="java",
                enabled=False,
            ),
        },
    )

    response = await client.get(
        "/api/v1/alert-groups",
        headers={
            "Authorization": f"Bearer {make_jwt(role='admin')}",
            "X-Project-Id": "disabled",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "项目不存在", "data": None}


async def test_alert_detail_rca_id_and_logs_are_project_scoped(
    app_instance,
    client,
    auth_headers,
    alerts_projects_config,
):
    async with app_instance.state.sessionmaker() as session:
        run = await AgentRunRepository(session, "prod").create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="message-service",
                anomaly_type="HighCPU",
                confidence=88,
                root_cause_summary="CPU saturation",
                evidence_chain={"metrics": ["cpu high"]},
            )
        )
        await session.commit()
    alert_id = await _seed_alert(
        app_instance,
        name="HighCPU",
        fingerprint="fp-rca",
        severity="critical",
        service="message-service",
        namespace="prod-ns",
        agent_run_id=run.id,
    )
    await app_instance.state.redis.set(
        RedisKey.of("prod", RedisKey.RECENT_ERRORS),
        '[{"timestamp":"2026-06-17T10:00:00Z","level":"ERROR",'
        '"service":"message-service","namespace":"prod-ns","message":"cpu high"},'
        '{"timestamp":"2026-06-17T10:01:00Z","level":"ERROR",'
        '"service":"other-service","namespace":"prod-ns","message":"ignore"}]',
    )
    await app_instance.state.redis.set(
        RedisKey.of("stage", RedisKey.RECENT_ERRORS),
        '[{"timestamp":"2026-06-17T10:02:00Z","level":"ERROR",'
        '"service":"message-service","namespace":"prod-ns","message":"stage leak"}]',
    )

    rca = await client.get(f"/api/v1/alerts/{alert_id}/rca-id", headers=auth_headers)
    logs = await client.get(f"/api/v1/alerts/{alert_id}/logs", headers=auth_headers)

    assert rca.status_code == 200
    assert rca.json()["data"] == {
        "rcaId": run.id,
        "summary": "CPU saturation",
        "confidenceScore": 88.0,
    }
    assert logs.status_code == 200
    assert logs.json()["data"]["total"] == 1
    assert logs.json()["data"]["items"][0]["message"] == "cpu high"
