from __future__ import annotations

import json

from sqlalchemy import select, text

from app.core.constants import RedisKey
from app.core.projects import ProjectConfig, ProjectsConfig
from app.models.alert_event import AlertEvent


async def _clean_alerts(app_instance) -> None:
    async with app_instance.state.sessionmaker() as session:
        await session.execute(text("TRUNCATE alert_events RESTART IDENTITY CASCADE"))
        await session.commit()


def _headers(token: str, project_id: str = "prod") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Project-Id": project_id}


def _set_projects(app_instance) -> None:
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


async def test_alert_webhook_enqueues_project_scoped_ingest(
    client,
    make_jwt,
    app_instance,
):
    _set_projects(app_instance)
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighCPU",
                    "service": "message-service",
                    "namespace": "production",
                    "pod": "message-0",
                    "severity": "critical",
                },
                "annotations": {"summary": "cpu high"},
            }
        ]
    }

    response = await client.post(
        "/api/v1/alerts/webhook",
        headers=_headers(make_jwt(role="admin"), "prod"),
        json=payload,
    )

    assert response.status_code == 202
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {"accepted": True},
    }
    queued = await app_instance.state.redis.rpop(
        RedisKey.of("prod", RedisKey.INGEST)
    )
    assert queued is not None
    assert json.loads(queued)["alerts"][0]["labels"]["service"] == "message-service"
    assert await app_instance.state.redis.rpop(RedisKey.of("stage", RedisKey.INGEST)) is None


async def test_alert_webhook_requires_known_enabled_project(
    client,
    make_jwt,
    app_instance,
):
    _set_projects(app_instance)
    token = make_jwt(role="admin")

    disabled = await client.post(
        "/api/v1/alerts/webhook",
        headers=_headers(token, "disabled"),
        json={"alerts": []},
    )

    assert disabled.status_code == 200
    assert disabled.json() == {"code": 40004, "message": "项目不存在", "data": None}


async def test_alert_webhook_fallback_to_project_id_in_labels(
    client,
    make_jwt,
    app_instance,
):
    _set_projects(app_instance)
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighCPU",
                    "project_id": "prod",
                    "service": "message-service",
                    "namespace": "production",
                    "pod": "message-0",
                    "severity": "critical",
                },
            }
        ]
    }

    response = await client.post(
        "/api/v1/alerts/webhook",
        headers={"Authorization": f"Bearer {make_jwt(role='admin')}"},
        json=payload,
    )

    assert response.status_code == 202
    queued = await app_instance.state.redis.rpop(
        RedisKey.of("prod", RedisKey.INGEST)
    )
    assert queued is not None


async def test_alert_webhook_fallback_to_default_project(
    client,
    make_jwt,
    app_instance,
):
    _set_projects(app_instance)
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighCPU",
                    "service": "message-service",
                    "namespace": "production",
                    "pod": "message-0",
                    "severity": "critical",
                },
            }
        ]
    }

    response = await client.post(
        "/api/v1/alerts/webhook",
        headers={"Authorization": f"Bearer {make_jwt(role='admin')}"},
        json=payload,
    )

    assert response.status_code == 202
    queued = await app_instance.state.redis.rpop(
        RedisKey.of("prod", RedisKey.INGEST)
    )
    assert queued is not None


async def test_alert_webhook_fails_when_no_project_resolvable(
    client,
    make_jwt,
    app_instance,
):
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(
                name="Prod", metric_profile="java", enabled=False
            ),
        },
    )
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "HighCPU"},
            }
        ]
    }

    response = await client.post(
        "/api/v1/alerts/webhook",
        headers={"Authorization": f"Bearer {make_jwt(role='admin')}"},
        json=payload,
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "项目不存在", "data": None}


async def test_process_ingest_payload_deduplicates_and_persists_alert(app_instance):
    from app.alerts.ingest import process_ingest_payload

    await _clean_alerts(app_instance)
    payload = {
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighCPU",
                    "service": "message-service",
                    "namespace": "production",
                    "pod": "message-0",
                    "severity": "critical",
                },
                "annotations": {"summary": "cpu high", "description": "cpu > 90"},
            },
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighCPU",
                    "service": "message-service",
                    "namespace": "production",
                    "pod": "message-0",
                    "severity": "critical",
                },
                "annotations": {"summary": "cpu still high"},
            },
        ]
    }

    async with app_instance.state.sessionmaker() as session:
        result = await process_ingest_payload(session, "prod", payload)
        await session.commit()

    assert result == {"created": 1, "updated": 1, "ignored": 0}
    async with app_instance.state.sessionmaker() as session:
        rows = (await session.execute(select(AlertEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].project_id == "prod"
    assert rows[0].name == "HighCPU"
    assert rows[0].service == "message-service"
    assert rows[0].namespace == "production"
    assert rows[0].pod == "message-0"
    assert rows[0].severity == "critical"
    assert rows[0].alert_count == 2


async def test_process_ingest_payload_ignores_malformed_alerts(app_instance):
    from app.alerts.ingest import process_ingest_payload

    await _clean_alerts(app_instance)
    async with app_instance.state.sessionmaker() as session:
        result = await process_ingest_payload(
            session,
            "prod",
            {"alerts": [{"status": "firing", "labels": {"service": "message-service"}}]},
        )
        await session.commit()

    assert result == {"created": 0, "updated": 0, "ignored": 1}
