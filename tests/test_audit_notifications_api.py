from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.projects import ProjectConfig, ProjectsConfig
from app.repositories.audit import AuditLogCreate, AuditLogRepository
from app.repositories.notifications import NotificationCreate, NotificationRepository


@pytest.fixture(autouse=True)
async def _clean_audit_notifications_api_tables(app_instance):
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
def audit_notifications_projects_config(app_instance):
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


async def _seed_audit(
    app_instance,
    *,
    project_id: str | None = "prod",
    operator_type: str = "admin",
    operator_name: str = "admin",
    action: str = "ALERT_SUPPRESS",
    resource_type: str = "alert",
    resource_id: str | None = "alert-1",
    result: str = "success",
    reason: str | None = "manual operation",
    before_state: dict | None = None,
    after_state: dict | None = None,
    ip_address: str | None = "127.0.0.1",
    created_at: datetime | None = None,
) -> int:
    async with app_instance.state.sessionmaker() as session:
        row = await AuditLogRepository(session, project_id).create(
            AuditLogCreate(
                operator_type=operator_type,
                operator_name=operator_name,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                result=result,
                reason=reason,
                before_state=before_state,
                after_state=after_state,
                ip_address=ip_address,
            )
        )
        if created_at is not None:
            row.created_at = created_at
        await session.commit()
        return row.id


async def _seed_notification(
    app_instance,
    *,
    project_id: str = "prod",
    type: str = "alert",
    title: str = "New alert",
    message: str = "message-service is firing",
    read: bool = False,
    created_at: datetime | None = None,
) -> int:
    async with app_instance.state.sessionmaker() as session:
        row = await NotificationRepository(session, project_id).create(
            NotificationCreate(type=type, title=title, message=message, read=read)
        )
        if created_at is not None:
            row.created_at = created_at
        await session.commit()
        return row.id


async def test_audit_and_notifications_require_admin_jwt(client):
    audit_response = await client.get(
        "/api/v1/audit/stats",
        headers={"X-Project-Id": "prod"},
    )
    notifications_response = await client.get(
        "/api/v1/notifications",
        headers={"X-Project-Id": "prod"},
    )

    assert audit_response.status_code == 200
    assert audit_response.json()["code"] == 40001
    assert notifications_response.status_code == 200
    assert notifications_response.json()["code"] == 40001


async def test_audit_and_notifications_require_known_enabled_project(
    client,
    make_jwt,
    audit_notifications_projects_config,
):
    headers = {"Authorization": f"Bearer {make_jwt(role='admin')}"}

    missing = await client.get("/api/v1/audit/logs", headers=headers)
    disabled = await client.get(
        "/api/v1/notifications",
        headers={**headers, "X-Project-Id": "disabled"},
    )

    assert missing.status_code == 200
    assert missing.json() == {"code": 40004, "message": "项目不存在", "data": None}
    assert disabled.status_code == 200
    assert disabled.json() == missing.json()


async def test_audit_stats_counts_today_by_project_without_global_rows(
    app_instance,
    client,
    auth_headers,
    audit_notifications_projects_config,
):
    today = datetime.now(UTC)
    yesterday = today - timedelta(days=1)
    await _seed_audit(app_instance, operator_type="aiops_agent", created_at=today)
    await _seed_audit(app_instance, operator_type="admin", created_at=today)
    await _seed_audit(app_instance, result="failed", created_at=today)
    await _seed_audit(app_instance, created_at=yesterday)
    await _seed_audit(app_instance, project_id="stage", created_at=today)
    await _seed_audit(app_instance, project_id=None, operator_type="admin", created_at=today)

    response = await client.get("/api/v1/audit/stats", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {
            "todayTotal": 3,
            "aiopsAgentCount": 1,
            "adminCount": 2,
            "failedCount": 1,
        },
    }


async def test_audit_logs_filters_project_and_returns_frontend_shape(
    app_instance,
    client,
    auth_headers,
    audit_notifications_projects_config,
):
    target_time = datetime(2026, 6, 17, 9, 30, tzinfo=UTC)
    expected_id = await _seed_audit(
        app_instance,
        operator_type="admin",
        operator_name="alice",
        action="ALERT_SUPPRESS",
        resource_type="alert",
        resource_id="HighCPU",
        result="success",
        reason="maintenance window",
        before_state={"status": "firing"},
        after_state={"status": "suppressed"},
        ip_address="10.0.0.1",
        created_at=target_time,
    )
    await _seed_audit(
        app_instance,
        action="ALERT_RESOLVE",
        resource_id="OtherAlert",
        created_at=target_time + timedelta(minutes=1),
    )
    await _seed_audit(
        app_instance,
        project_id="stage",
        action="ALERT_SUPPRESS",
        resource_id="HighCPU",
        created_at=target_time,
    )
    await _seed_audit(
        app_instance,
        project_id=None,
        action="ALERT_SUPPRESS",
        resource_id="HighCPU",
        created_at=target_time,
    )

    response = await client.get(
        "/api/v1/audit/logs?keyword=cpu&operatorType=admin&action=ALERT_SUPPRESS"
        "&timeRange=custom&startTime=2026-06-17T00:00:00Z"
        "&endTime=2026-06-18T00:00:00Z&result=success&page=1&pageSize=10",
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
            "operatorType": "admin",
            "operator": "alice",
            "action": "ALERT_SUPPRESS",
            "targetObject": "alert",
            "targetResource": "HighCPU",
            "result": "success",
            "ipAddress": "10.0.0.1",
            "timestamp": "2026-06-17T09:30:00Z",
            "details": "maintenance window",
        }
    ]


async def test_audit_logs_time_range_filters_relative_windows(
    app_instance,
    client,
    auth_headers,
    audit_notifications_projects_config,
):
    recent_id = await _seed_audit(
        app_instance,
        action="HEAL_EXECUTE",
        resource_id="recent-action",
        created_at=datetime.now(UTC) - timedelta(days=2),
    )
    await _seed_audit(
        app_instance,
        action="HEAL_EXECUTE",
        resource_id="old-action",
        created_at=datetime.now(UTC) - timedelta(days=8),
    )

    response = await client.get(
        "/api/v1/audit/logs?timeRange=7d&page=1&pageSize=20",
        headers=auth_headers,
    )

    assert response.status_code == 200
    page = response.json()["data"]
    assert page["total"] == 1
    assert [item["id"] for item in page["items"]] == [str(recent_id)]


async def test_audit_detail_is_project_scoped(
    app_instance,
    client,
    auth_headers,
    audit_notifications_projects_config,
):
    prod_id = await _seed_audit(app_instance, action="ALERT_ACKNOWLEDGE")
    stage_id = await _seed_audit(app_instance, project_id="stage", action="ALERT_RESOLVE")

    prod_response = await client.get(f"/api/v1/audit/logs/{prod_id}", headers=auth_headers)
    stage_response = await client.get(f"/api/v1/audit/logs/{stage_id}", headers=auth_headers)
    missing_response = await client.get("/api/v1/audit/logs/99999", headers=auth_headers)

    assert prod_response.status_code == 200
    assert prod_response.json()["code"] == 0
    assert prod_response.json()["data"]["id"] == str(prod_id)
    assert stage_response.status_code == 200
    assert stage_response.json() == {"code": 40004, "message": "审计日志不存在", "data": None}
    assert missing_response.status_code == 200
    assert missing_response.json() == stage_response.json()


async def test_notifications_list_counts_unread_limits_and_filters_project(
    app_instance,
    client,
    auth_headers,
    audit_notifications_projects_config,
):
    newest_time = datetime(2026, 6, 17, 11, 0, tzinfo=UTC)
    expected_id = await _seed_notification(
        app_instance,
        type="healing",
        title="Healing completed",
        message="pod restarted",
        read=False,
        created_at=newest_time,
    )
    await _seed_notification(
        app_instance,
        title="Old read notification",
        message="read message",
        read=True,
        created_at=newest_time - timedelta(minutes=1),
    )
    await _seed_notification(
        app_instance,
        title="Older unread notification",
        message="unread message",
        read=False,
        created_at=newest_time - timedelta(minutes=2),
    )
    await _seed_notification(
        app_instance,
        project_id="stage",
        title="Stage notification",
        message="stage message",
        read=False,
        created_at=newest_time + timedelta(minutes=1),
    )

    response = await client.get("/api/v1/notifications?limit=2", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {
            "unreadCount": 2,
            "items": [
                {
                    "id": str(expected_id),
                    "type": "healing",
                    "title": "Healing completed",
                    "message": "pod restarted",
                    "time": "2026-06-17T11:00:00Z",
                    "read": False,
                },
                {
                    "id": "2",
                    "type": "alert",
                    "title": "Old read notification",
                    "message": "read message",
                    "time": "2026-06-17T10:59:00Z",
                    "read": True,
                },
            ],
        },
    }


async def test_notification_read_mutates_current_project_only(
    app_instance,
    client,
    auth_headers,
    audit_notifications_projects_config,
):
    prod_id = await _seed_notification(app_instance, read=False)
    stage_id = await _seed_notification(app_instance, project_id="stage", read=False)

    prod_response = await client.post(
        f"/api/v1/notifications/{prod_id}/read",
        headers=auth_headers,
    )
    stage_response = await client.post(
        f"/api/v1/notifications/{stage_id}/read",
        headers=auth_headers,
    )

    assert prod_response.status_code == 200
    assert prod_response.json() == {"code": 0, "message": "success", "data": {"success": True}}
    assert stage_response.status_code == 200
    assert stage_response.json() == {"code": 40004, "message": "通知不存在", "data": None}
    async with app_instance.state.sessionmaker() as session:
        prod = await NotificationRepository(session, "prod").get(prod_id)
        stage = await NotificationRepository(session, "stage").get(stage_id)
    assert prod is not None
    assert prod.read is True
    assert stage is not None
    assert stage.read is False


async def test_audit_null_project_repository_behavior_still_only_returns_null_rows(
    app_instance,
    audit_notifications_projects_config,
):
    global_id = await _seed_audit(app_instance, project_id=None, action="USER_BAN")
    await _seed_audit(app_instance, project_id="prod", action="ALERT_SUPPRESS")

    async with app_instance.state.sessionmaker() as session:
        global_rows = await AuditLogRepository(session, None).list(page=1, page_size=20)
        global_row = await AuditLogRepository(session, None).get(global_id)

    assert global_row is not None
    assert [row.project_id for row in global_rows.items] == [None]
