import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from app.core.constants import RedisKey
from app.core.config import get_settings
from app.core.projects import ProjectConfig, ProjectsConfig
from app.models.agent_run import AgentRun
from app.models.alert_event import AlertEvent
from app.models.audit_log import AuditLog
from app.models.heal_action import HealAction
from app.models.notification import Notification


@pytest.fixture(autouse=True)
async def _clean_seed_api_tables(app_instance):
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "TRUNCATE notifications, audit_logs, heal_actions, "
                "agent_runs, alert_events RESTART IDENTITY"
            )
        )
        await session.commit()
    yield
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "TRUNCATE notifications, audit_logs, heal_actions, "
                "agent_runs, alert_events RESTART IDENTITY"
            )
        )
        await session.commit()


@pytest.fixture
def seed_projects_config(app_instance):
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


async def test_seed_demo_creates_demo_data_for_current_project(
    app_instance,
    client,
    auth_headers,
    seed_projects_config,
):
    response = await client.post(
        "/api/v1/seeds/demo",
        headers=auth_headers,
        json={"reset": True},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0
    counts = response.json()["data"]
    assert counts["alerts"] >= 1
    assert counts["agentRuns"] >= 1
    assert counts["notifications"] >= 1
    assert counts["auditLogs"] >= 1
    assert counts["logs"] >= 2

    async with app_instance.state.sessionmaker() as session:
        alert = (
            await session.execute(
                select(AlertEvent).where(AlertEvent.project_id == "prod")
            )
        ).scalar_one()
        run = (
            await session.execute(
                select(AgentRun).where(AgentRun.project_id == "prod")
            )
        ).scalar_one()
        assert alert.status == "firing"
        assert alert.service == "message-service"
        assert "JVM" in alert.name
        assert run.status == "completed"
        assert run.evidence_chain
        assert run.timeline
        assert run.root_cause_summary

    raw_logs = await app_instance.state.redis.get(
        RedisKey.of("prod", RedisKey.RECENT_ERRORS)
    )
    logs = json.loads(raw_logs)
    assert len(logs) >= 2
    assert any("OutOfMemoryError" in item["message"] for item in logs)


async def test_seed_demo_requires_auth_and_project(
    client,
    make_jwt,
    seed_projects_config,
):
    missing_auth = await client.post(
        "/api/v1/seeds/demo",
        headers={"X-Project-Id": "prod"},
        json={"reset": True},
    )
    assert missing_auth.status_code == 200
    assert missing_auth.json()["code"] == 40001

    missing_project = await client.post(
        "/api/v1/seeds/demo",
        headers={"Authorization": f"Bearer {make_jwt(role='admin')}"},
        json={"reset": True},
    )
    assert missing_project.status_code == 200
    assert missing_project.json()["code"] == 40004


async def test_seed_demo_reset_only_deletes_current_project_data(
    app_instance,
    client,
    auth_headers,
    seed_projects_config,
):
    async with app_instance.state.sessionmaker() as session:
        session.add(
            AlertEvent(
                project_id="stage",
                fingerprint="stage-alert",
                name="StageMemory",
                severity="critical",
                status="firing",
                service="stage-service",
                labels={},
                annotations={},
            )
        )
        session.add(
            AgentRun(
                project_id="stage",
                trigger_source="manual",
                status="completed",
                evidence_chain={"metrics": []},
                timeline=[{"event": "stage"}],
            )
        )
        session.add(
            Notification(
                project_id="stage",
                type="alert",
                title="stage notification",
                message="stage message",
            )
        )
        session.add(
            AuditLog(
                project_id="stage",
                operator_type="admin",
                operator_name="stage-admin",
                action="STAGE_ACTION",
                resource_type="alert",
                result="success",
            )
        )
        session.add(
            HealAction(
                project_id="stage",
                action_type="pod_restart",
                target_resource="deployment/stage-service",
                status="success",
                risk_level="medium",
                operator="aiops_agent",
            )
        )
        await session.commit()

    response = await client.post(
        "/api/v1/seeds/demo",
        headers=auth_headers,
        json={"reset": True},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0

    async with app_instance.state.sessionmaker() as session:
        for model in (AlertEvent, AgentRun, Notification, AuditLog, HealAction):
            count = await session.scalar(
                select(func.count()).select_from(model).where(model.project_id == "stage")
            )
            assert count == 1


async def test_seed_demo_is_disabled_in_production(monkeypatch, make_jwt):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    get_settings.cache_clear()

    from app.main import get_app

    application = get_app()
    async with application.router.lifespan_context(application):
        application.state.projects_config = ProjectsConfig(
            default_project="prod",
            projects={"prod": ProjectConfig(name="Prod", metric_profile="java")},
        )
        async with application.state.sessionmaker() as session:
            session.add(
                AuditLog(
                    project_id=None,
                    operator_type="system",
                    operator_name="global",
                    action="GLOBAL_ACTION",
                    resource_type="system",
                    result="success",
                )
            )
            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as prod_client:
            response = await prod_client.post(
                "/api/v1/seeds/demo",
                headers={
                    "Authorization": f"Bearer {make_jwt(role='admin')}",
                    "X-Project-Id": "prod",
                },
                json={"reset": True},
            )

        assert response.status_code == 200
        assert response.json() == {
            "code": 40003,
            "message": "生产环境禁用种子数据接口",
            "data": None,
        }

        async with application.state.sessionmaker() as session:
            alert_count = await session.scalar(
                select(func.count()).select_from(AlertEvent)
            )
            run_count = await session.scalar(select(func.count()).select_from(AgentRun))
            global_audit_count = await session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.project_id.is_(None))
            )
            assert alert_count == 0
            assert run_count == 0
            assert global_audit_count == 1
        assert (
            await application.state.redis.get(
                RedisKey.of("prod", RedisKey.RECENT_ERRORS)
            )
            is None
        )
