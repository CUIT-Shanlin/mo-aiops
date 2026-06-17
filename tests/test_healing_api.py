from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.core.projects import ProjectConfig, ProjectsConfig
from app.models.audit_log import AuditLog
from app.repositories.alerts import AlertEventCreate, AlertEventRepository
from app.repositories.healing import HealActionCreate, HealActionRepository


@pytest.fixture(autouse=True)
async def _clean_healing_api_tables(app_instance):
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


@pytest.fixture
def healing_projects_config(app_instance):
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
            "disabled": ProjectConfig(
                name="Disabled",
                metric_profile="java",
                enabled=False,
            ),
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
):
    async with app_instance.state.sessionmaker() as session:
        alert = await AlertEventRepository(session, project_id).create(
            AlertEventCreate(
                name=name,
                fingerprint=fingerprint,
                severity="critical",
                status="processing",
                service="message-service",
                namespace="prod-ns",
                pod="message-0",
            )
        )
        await session.commit()
        return alert.id


async def _seed_action(
    app_instance,
    *,
    project_id: str = "prod",
    alert_name: str = "HighCPU",
    alert_fingerprint: str = "fp-high-cpu",
    action_type: str = "pod_restart",
    target_resource: str = "pod/message-0",
    target_namespace: str | None = "prod-ns",
    status: str = "pending",
    risk_level: str = "low",
    operator: str = "aiops_agent",
    result_message: str | None = None,
    executed_at: datetime | None = None,
    finished_at: datetime | None = None,
):
    alert_id = await _seed_alert(
        app_instance,
        project_id=project_id,
        name=alert_name,
        fingerprint=alert_fingerprint,
    )
    async with app_instance.state.sessionmaker() as session:
        action = await HealActionRepository(session, project_id).create(
            HealActionCreate(
                alert_event_id=alert_id,
                action_type=action_type,
                target_resource=target_resource,
                target_namespace=target_namespace,
                status=status,
                risk_level=risk_level,
                operator=operator,
                result_message=result_message,
                executed_at=executed_at,
                finished_at=finished_at,
            )
        )
        await session.commit()
        return action.id


async def test_healing_api_requires_admin_jwt(client):
    response = await client.get("/api/v1/healing/stats", headers={"X-Project-Id": "prod"})

    assert response.status_code == 200
    assert response.json()["code"] == 40001


async def test_healing_api_requires_known_enabled_project(
    client,
    make_jwt,
    healing_projects_config,
):
    headers = {"Authorization": f"Bearer {make_jwt(role='admin')}"}

    missing = await client.get("/api/v1/healing/actions", headers=headers)
    disabled = await client.get(
        "/api/v1/healing/actions",
        headers={**headers, "X-Project-Id": "disabled"},
    )

    assert missing.status_code == 200
    assert missing.json() == {"code": 40004, "message": "项目不存在", "data": None}
    assert disabled.status_code == 200
    assert disabled.json() == missing.json()


async def test_list_healing_actions_filters_project_and_returns_frontend_shape(
    app_instance,
    client,
    auth_headers,
    healing_projects_config,
):
    start = datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
    end = start + timedelta(seconds=73)
    expected_id = await _seed_action(
        app_instance,
        alert_name="PodCrashLoopBackOff",
        alert_fingerprint="fp-target",
        action_type="rolling_restart",
        target_resource="deployment/message-service",
        status="waiting_approval",
        risk_level="high",
        operator="admin",
        result_message="waiting for approval",
        executed_at=start,
        finished_at=end,
    )
    await _seed_action(
        app_instance,
        alert_fingerprint="fp-filtered",
        action_type="hpa_scale",
        status="success",
        risk_level="medium",
    )
    await _seed_action(
        app_instance,
        project_id="stage",
        alert_fingerprint="fp-stage",
        action_type="rolling_restart",
        status="waiting_approval",
        risk_level="high",
    )

    response = await client.get(
        "/api/v1/healing/actions?status=waiting_approval&riskLevel=high"
        "&page=1&pageSize=10",
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
            "id": str(expected_id),
            "alertName": "PodCrashLoopBackOff",
            "actionType": "rolling_restart",
            "targetResource": "deployment/message-service",
            "namespace": "prod-ns",
            "status": "waiting_approval",
            "riskLevel": "high",
            "executor": "管理员",
            "startTime": "2026-06-17T10:00:00Z",
            "endTime": "2026-06-17T10:01:13Z",
            "duration": "1分13秒",
            "result": "waiting for approval",
            "impactScope": "需人工确认影响范围",
            "estimatedRecovery": "3-5 分钟",
        }
    ]


async def test_healing_stats_counts_success_failed_and_rate(
    app_instance,
    client,
    auth_headers,
    healing_projects_config,
):
    await _seed_action(app_instance, alert_fingerprint="fp1", status="success")
    await _seed_action(app_instance, alert_fingerprint="fp2", status="verified")
    await _seed_action(app_instance, alert_fingerprint="fp3", status="failed")
    await _seed_action(app_instance, alert_fingerprint="fp4", status="pending")
    await _seed_action(
        app_instance,
        project_id="stage",
        alert_fingerprint="fp-stage",
        status="failed",
    )

    response = await client.get("/api/v1/healing/stats", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {
            "todayTotal": 4,
            "successCount": 2,
            "failedCount": 1,
            "successRate": 50.0,
        },
    }


async def test_healing_stats_only_counts_today_actions(
    app_instance,
    client,
    auth_headers,
    healing_projects_config,
):
    today_id = await _seed_action(
        app_instance,
        alert_fingerprint="fp-today",
        status="success",
    )
    yesterday_id = await _seed_action(
        app_instance,
        alert_fingerprint="fp-yesterday",
        status="failed",
    )
    async with app_instance.state.sessionmaker() as session:
        yesterday = await HealActionRepository(session, "prod").get(yesterday_id)
        today = await HealActionRepository(session, "prod").get(today_id)
        assert yesterday is not None
        assert today is not None
        yesterday.created_at = datetime.now(UTC) - timedelta(days=1)
        today.created_at = datetime.now(UTC)
        await session.commit()

    response = await client.get("/api/v1/healing/stats", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "todayTotal": 1,
        "successCount": 1,
        "failedCount": 0,
        "successRate": 100.0,
    }


async def test_healing_stats_uses_utc_day_boundaries(
    app_instance,
    client,
    auth_headers,
    healing_projects_config,
):
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    before_today_id = await _seed_action(
        app_instance,
        alert_fingerprint="fp-before-today",
        status="failed",
    )
    at_start_id = await _seed_action(
        app_instance,
        alert_fingerprint="fp-at-start",
        status="success",
    )
    next_start_id = await _seed_action(
        app_instance,
        alert_fingerprint="fp-next-start",
        status="failed",
    )
    async with app_instance.state.sessionmaker() as session:
        before_today = await HealActionRepository(session, "prod").get(before_today_id)
        at_start = await HealActionRepository(session, "prod").get(at_start_id)
        next_start = await HealActionRepository(session, "prod").get(next_start_id)
        assert before_today is not None
        assert at_start is not None
        assert next_start is not None
        before_today.created_at = today_start - timedelta(seconds=1)
        at_start.created_at = today_start
        next_start.created_at = today_start + timedelta(days=1)
        await session.commit()

    response = await client.get("/api/v1/healing/stats", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "todayTotal": 1,
        "successCount": 1,
        "failedCount": 0,
        "successRate": 100.0,
    }


async def test_pending_approval_returns_earliest_one_or_null(
    app_instance,
    client,
    auth_headers,
    healing_projects_config,
):
    first_id = await _seed_action(
        app_instance,
        alert_name="FirstRisk",
        alert_fingerprint="fp-first",
        status="waiting_approval",
        risk_level="high",
    )
    await _seed_action(
        app_instance,
        alert_name="SecondRisk",
        alert_fingerprint="fp-second",
        status="waiting_approval",
        risk_level="high",
    )

    response = await client.get(
        "/api/v1/healing/actions/pending-approval",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(first_id)
    async with app_instance.state.sessionmaker() as session:
        action = await HealActionRepository(session, "prod").get(first_id)
        assert action is not None
        action.status = "failed"
        next_action = await HealActionRepository(session, "prod").get(first_id + 1)
        assert next_action is not None
        next_action.status = "failed"
        await session.commit()

    empty = await client.get(
        "/api/v1/healing/actions/pending-approval",
        headers=auth_headers,
    )

    assert empty.status_code == 200
    assert empty.json() == {"code": 0, "message": "success", "data": None}


async def test_approve_and_reject_mutate_status_and_write_audit(
    app_instance,
    client,
    auth_headers,
    healing_projects_config,
):
    approve_id = await _seed_action(
        app_instance,
        alert_fingerprint="fp-approve",
        action_type="node_evict",
        status="waiting_approval",
        risk_level="high",
    )
    reject_id = await _seed_action(
        app_instance,
        alert_fingerprint="fp-reject",
        action_type="rolling_restart",
        status="waiting_approval",
        risk_level="high",
    )

    approved = await client.post(
        f"/api/v1/healing/actions/{approve_id}/approve",
        json={"approverNote": "approved from bridge"},
        headers=auth_headers,
    )
    rejected = await client.post(
        f"/api/v1/healing/actions/{reject_id}/reject",
        json={"reason": "too risky"},
        headers=auth_headers,
    )

    assert approved.status_code == 200
    assert approved.json() == {
        "code": 0,
        "message": "success",
        "data": {"success": True, "taskId": str(approve_id)},
    }
    assert rejected.status_code == 200
    assert rejected.json() == {
        "code": 0,
        "message": "success",
        "data": {"success": True},
    }
    async with app_instance.state.sessionmaker() as session:
        approved_action = await HealActionRepository(session, "prod").get(approve_id)
        rejected_action = await HealActionRepository(session, "prod").get(reject_id)
        audit_rows = (
            await session.execute(
                select(AuditLog.action, AuditLog.resource_id, AuditLog.reason)
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id)
            )
        ).all()

    assert approved_action is not None
    assert approved_action.status == "pending"
    assert approved_action.approved_by == "u1"
    assert approved_action.approver_note == "approved from bridge"
    assert rejected_action is not None
    assert rejected_action.status == "failed"
    assert rejected_action.result_message == "too risky"
    assert audit_rows == [
        ("HEAL_APPROVE", str(approve_id), "approved from bridge"),
        ("HEAL_CANCEL", str(reject_id), "too risky"),
    ]


async def test_healing_actions_are_project_scoped_for_mutations(
    app_instance,
    client,
    auth_headers,
    healing_projects_config,
):
    stage_id = await _seed_action(
        app_instance,
        project_id="stage",
        alert_fingerprint="fp-stage",
        status="waiting_approval",
        risk_level="high",
    )

    response = await client.post(
        f"/api/v1/healing/actions/{stage_id}/approve",
        json={"approverNote": "wrong project"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "自愈操作不存在", "data": None}
    async with app_instance.state.sessionmaker() as session:
        stage_action = await HealActionRepository(session, "stage").get(stage_id)
        audits = (
            await session.execute(select(AuditLog).where(AuditLog.project_id == "prod"))
        ).scalars().all()

    assert stage_action is not None
    assert stage_action.status == "waiting_approval"
    assert audits == []


async def test_healing_status_endpoint_returns_frontend_shape(
    app_instance,
    client,
    auth_headers,
    healing_projects_config,
):
    start = datetime(2026, 6, 17, 11, 0, tzinfo=UTC)
    end = start + timedelta(seconds=5)
    action_id = await _seed_action(
        app_instance,
        alert_fingerprint="fp-status",
        status="success",
        result_message="pod restarted",
        executed_at=start,
        finished_at=end,
    )

    response = await client.get(
        f"/api/v1/healing/actions/{action_id}/status",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {
            "status": "success",
            "result": "pod restarted",
            "endTime": "2026-06-17T11:00:05Z",
            "duration": "5秒",
        },
    }
