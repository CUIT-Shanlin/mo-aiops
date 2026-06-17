from __future__ import annotations

from types import SimpleNamespace

import pytest

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.projects import ProjectConfig, ProjectsConfig
from app.collectors.windows import MetricWindowStore, TraceCache
from app.agent.runs import AgentRunner
from app.models.heal_action import HealAction
from app.repositories.alerts import AlertEventCreate, AlertEventRepository
from app.repositories.agent_runs import AgentRunCreate, AgentRunRepository


async def _clean_agent_runs(app_instance) -> None:
    async with app_instance.state.sessionmaker() as session:
        await session.execute(text("DELETE FROM agent_runs"))
        await session.commit()


async def test_similar_cases_requires_admin_jwt(client):
    response = await client.get(
        "/api/v1/agent/rag/similar-cases?alertType=CrashLoopBackOff&service=message-service"
    )

    assert response.status_code == 200
    assert response.json()["code"] == 40001


async def test_trigger_persists_run_and_returns_summary(app_instance, make_jwt):
    await _clean_agent_runs(app_instance)
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={"prod": ProjectConfig(name="Prod", metric_profile="java")},
    )

    async def fake_build_agent_graph(context):
        assert context.session is not None
        assert context.redis is app_instance.state.redis
        assert context.metric_window_store is app_instance.state.metric_window_store
        assert context.trace_cache is app_instance.state.trace_cache

        class FakeGraph:
            async def ainvoke(self, initial_state):
                assert initial_state.project_id == "prod"
                return {
                    "project_id": "prod",
                    "trigger_source": "manual",
                    "anomaly_detected": True,
                    "fault_service": "message-service",
                    "anomaly_type": "CrashLoopBackOff",
                    "severity": "critical",
                    "confidence": 91.25,
                    "root_cause_summary": "message-service pod repeatedly crashed",
                    "conclusion": {
                        "fault_service": "message-service",
                        "anomaly_type": "CrashLoopBackOff",
                        "severity": "critical",
                        "confidence": 91.25,
                        "root_cause_summary": "message-service pod repeatedly crashed",
                    },
                    "healing_decision": {
                        "action_type": "pod_restart",
                        "target_resource": "message-service",
                        "auto_heal": True,
                        "risk_level": "medium",
                    },
                    "node_states": {"decide_heal_node": {"status": "success"}},
                    "evidence_chain": {"metrics": [], "logs": [], "traces": []},
                    "timeline": [{"type": "heal_decision"}],
                    "rag_results": [],
                    "stats": {
                        "llm_calls_count": 0,
                        "analyzed_logs_count": 1,
                        "related_traces_count": 2,
                    },
                }

        return FakeGraph()

    import app.agent.runs as runs_module

    original = runs_module.build_agent_graph
    runs_module.build_agent_graph = fake_build_agent_graph
    try:
        transport = ASGITransport(app=app_instance)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/agent/trigger",
                headers={
                    "Authorization": f"Bearer {make_jwt(role='admin')}",
                    "X-Project-Id": "prod",
                },
            )
    finally:
        runs_module.build_agent_graph = original

    assert response.status_code == 200
    assert response.json()["code"] == 0
    summary = response.json()["data"]
    assert summary == {
        "runId": summary["runId"],
        "status": "completed",
        "anomalyDetected": True,
        "faultService": "message-service",
        "anomalyType": "CrashLoopBackOff",
        "severity": "critical",
        "confidence": 91.25,
    }

    async with app_instance.state.sessionmaker() as session:
        row = await AgentRunRepository(session, "prod").get(summary["runId"])
        assert row is not None
        assert row.status == "completed"
        assert row.trigger_source == "manual"
        assert row.action_type == "pod_restart"
        assert row.risk_level == "medium"
        assert row.node_states["decide_heal_node"]["status"] == "success"


async def test_agent_runner_marks_failed_run_and_preserves_original_error(
    app_instance,
    monkeypatch,
):
    await _clean_agent_runs(app_instance)

    class FailingGraph:
        async def ainvoke(self, initial_state):
            raise RuntimeError("graph exploded")

    async def fake_build_agent_graph(context):
        return FailingGraph()

    import app.agent.runs as runs_module

    monkeypatch.setattr(runs_module, "build_agent_graph", fake_build_agent_graph)

    async with app_instance.state.sessionmaker() as session:
        runner = AgentRunner(
            session=session,
            project_id="prod",
            redis=app_instance.state.redis,
            metric_window_store=MetricWindowStore(),
            trace_cache=TraceCache(),
        )
        try:
            await runner.run_once(trigger_source="manual")
        except RuntimeError as exc:
            assert str(exc) == "graph exploded"
        else:
            raise AssertionError("expected graph failure")

    async with app_instance.state.sessionmaker() as session:
        rows = await AgentRunRepository(session, "prod").list_failed(limit=10)

    assert len(rows) == 1
    assert rows[0].status == "failed"


async def test_similar_cases_returns_list_shape(app_instance, make_jwt):
    await _clean_agent_runs(app_instance)
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={"prod": ProjectConfig(name="Prod", metric_profile="java")},
    )
    async with app_instance.state.sessionmaker() as session:
        await AgentRunRepository(session, "prod").create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="message-service",
                anomaly_type="CrashLoopBackOff",
                severity="critical",
                root_cause_summary="JVM OOM caused restart",
            )
        )
        await session.commit()

    response = await AsyncClient(
        transport=ASGITransport(app=app_instance),
        base_url="http://test",
    ).__aenter__()
    try:
        result = await response.get(
            "/api/v1/agent/rag/similar-cases?alertType=CrashLoopBackOff&service=message-service",
            headers={"Authorization": f"Bearer {make_jwt(role='admin')}", "X-Project-Id": "prod"},
        )
    finally:
        await response.__aexit__(None, None, None)

    assert result.status_code == 200
    assert result.json()["code"] == 0
    cases = result.json()["data"]
    assert len(cases) == 1
    assert cases[0].keys() == {
        "caseId",
        "title",
        "similarity",
        "rootCause",
        "resolution",
        "resolvedAt",
    }
    assert cases[0]["title"] == "message-service CrashLoopBackOff"


async def test_similar_cases_ignores_project_id_query_and_uses_header_project(
    app_instance,
    make_jwt,
):
    await _clean_agent_runs(app_instance)
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
        },
    )
    async with app_instance.state.sessionmaker() as session:
        await AgentRunRepository(session, "prod").create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="message-service",
                anomaly_type="CrashLoopBackOff",
                severity="critical",
                root_cause_summary="prod default case",
            )
        )
        await AgentRunRepository(session, "stage").create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="message-service",
                anomaly_type="CrashLoopBackOff",
                severity="critical",
                root_cause_summary="stage query case",
            )
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app_instance),
        base_url="http://test",
    ) as client:
        result = await client.get(
            "/api/v1/agent/rag/similar-cases"
            "?alertType=CrashLoopBackOff&service=message-service&project_id=stage",
            headers={"Authorization": f"Bearer {make_jwt(role='admin')}", "X-Project-Id": "prod"},
        )

    assert result.status_code == 200
    assert result.json()["code"] == 0
    assert result.json()["data"][0]["rootCause"] == "prod default case"


async def test_similar_cases_requires_project_header(client, make_jwt, app_instance):
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={"prod": ProjectConfig(name="Prod", metric_profile="java")},
    )

    response = await client.get(
        "/api/v1/agent/rag/similar-cases?alertType=CrashLoopBackOff&service=message-service",
        headers={"Authorization": f"Bearer {make_jwt(role='admin')}"},
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "项目不存在", "data": None}


@pytest.mark.parametrize("raw_project_id", ["", "unknown", "disabled"])
async def test_similar_cases_rejects_blank_unknown_and_disabled_project_header(
    client,
    make_jwt,
    app_instance,
    raw_project_id,
):
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "disabled": ProjectConfig(name="Disabled", metric_profile="java", enabled=False),
        },
    )

    response = await client.get(
        "/api/v1/agent/rag/similar-cases?alertType=CrashLoopBackOff&service=message-service",
        headers={
            "Authorization": f"Bearer {make_jwt(role='admin')}",
            "X-Project-Id": raw_project_id,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "项目不存在", "data": None}


async def test_trigger_rejects_missing_unknown_and_disabled_projects(
    client,
    make_jwt,
    app_instance,
):
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "disabled": ProjectConfig(name="Disabled", metric_profile="java", enabled=False),
        },
    )
    headers = {"Authorization": f"Bearer {make_jwt(role='admin')}"}

    query_project = await client.post(
        "/api/v1/agent/trigger?project_id=prod",
        headers=headers,
    )
    empty = await client.post(
        "/api/v1/agent/trigger",
        headers={**headers, "X-Project-Id": ""},
    )
    unknown = await client.post(
        "/api/v1/agent/trigger",
        headers={**headers, "X-Project-Id": "unknown"},
    )
    disabled = await client.post(
        "/api/v1/agent/trigger",
        headers={**headers, "X-Project-Id": "disabled"},
    )

    assert query_project.status_code == 200
    assert empty.status_code == 200
    assert unknown.status_code == 200
    assert disabled.status_code == 200
    assert query_project.json() == {"code": 40004, "message": "项目不存在", "data": None}
    assert empty.json() == {"code": 40004, "message": "项目不存在", "data": None}
    assert unknown.json() == {"code": 40004, "message": "项目不存在", "data": None}
    assert disabled.json() == {"code": 40004, "message": "项目不存在", "data": None}


async def test_main_initializes_shared_cache_for_collector_scheduler(monkeypatch):
    from app.main import lifespan

    created = {}

    class FakeScheduler:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.metric_window_store = kwargs["metric_window_store"]
            self.trace_cache = kwargs["trace_cache"]

        async def run_forever(self):
            return None

        async def stop(self):
            return None

    monkeypatch.setenv("STARTUP_COLLECTOR_ENABLED", "true")
    monkeypatch.setattr("app.main.create_engine", lambda url: SimpleNamespace(dispose=_async_noop))
    monkeypatch.setattr("app.main.make_sessionmaker", lambda engine: object())
    monkeypatch.setattr("app.main.create_redis", lambda url, db: SimpleNamespace(aclose=_async_noop))
    monkeypatch.setattr("app.main.run_upgrade_head", lambda: None)
    monkeypatch.setattr("app.main.ping_db", _async_true)
    monkeypatch.setattr("app.main.ping_redis", _async_true)
    monkeypatch.setattr(
        "app.main.load_projects_config",
        lambda: ProjectsConfig(
            default_project="prod",
            projects={"prod": ProjectConfig(name="Prod", metric_profile="java")},
        ),
    )
    monkeypatch.setattr("app.main.validate_configured_providers", _async_empty_dict)
    monkeypatch.setattr("app.main.CollectorScheduler", FakeScheduler)

    app = SimpleNamespace(state=SimpleNamespace())
    async with lifespan(app):
        assert app.state.metric_window_store is created["metric_window_store"]
        assert app.state.trace_cache is created["trace_cache"]
        assert app.state.collector_scheduler.metric_window_store is app.state.metric_window_store
        assert app.state.collector_scheduler.trace_cache is app.state.trace_cache


async def test_agent_console_read_endpoints_return_project_scoped_shapes(
    app_instance,
    client,
    make_jwt,
):
    await _clean_agent_runs(app_instance)
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
        },
    )
    async with app_instance.state.sessionmaker() as session:
        run = await AgentRunRepository(session, "prod").create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="message-service",
                anomaly_type="CrashLoopBackOff",
                severity="critical",
                confidence=92,
                root_cause_summary="JVM Heap 使用率异常升高",
                action_type="pod_restart",
                target_resource="pod/message-0",
                auto_heal=True,
                risk_level="medium",
                node_states={
                    "root_cause_node": {
                        "status": "success",
                        "duration": "2.1s",
                        "details": "根因定位完成",
                    }
                },
                evidence_chain={
                    "metrics": ["heap high"],
                    "logs": ["oom"],
                    "traces": ["slow span"],
                },
                timeline=[],
                rag_results=[],
                analyzed_logs_count=12,
                related_traces_count=3,
            )
        )
        await AgentRunRepository(session, "stage").create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="stage-service",
                anomaly_type="HighCPU",
                root_cause_summary="stage must not leak",
            )
        )
        await session.commit()
    headers = {
        "Authorization": f"Bearer {make_jwt(role='admin')}",
        "X-Project-Id": "prod",
    }

    stats = await client.get("/api/v1/agent/stats", headers=headers)
    current = await client.get("/api/v1/agent/current-task", headers=headers)
    workflow = await client.get("/api/v1/agent/workflow/steps", headers=headers)
    latest = await client.get("/api/v1/agent/latest-analysis", headers=headers)
    evidence = await client.get(
        f"/api/v1/agent/suggestions/{run.id}/evidence",
        headers=headers,
    )

    assert stats.json()["data"].keys() >= {
        "status",
        "currentTask",
        "pendingAlerts",
        "todayAnalysisCount",
        "adoptionRate",
    }
    assert current.json()["data"]["targetService"] == "message-service"
    assert workflow.json()["data"][0]["id"] == "root_cause_node"
    assert latest.json()["data"]["rootCause"] == "JVM Heap 使用率异常升高"
    assert latest.json()["data"]["suggestionStatus"] == "pending"
    assert evidence.json()["data"]["metrics"] == ["heap high"]


async def test_agent_suggestion_accept_and_reject_persist_status(
    app_instance,
    client,
    make_jwt,
):
    await _clean_agent_runs(app_instance)
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text("TRUNCATE audit_logs, heal_actions, alert_events RESTART IDENTITY CASCADE")
        )
        await session.commit()
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={"prod": ProjectConfig(name="Prod", metric_profile="java")},
    )
    async with app_instance.state.sessionmaker() as session:
        alert = await AlertEventRepository(session, "prod").create(
            AlertEventCreate(
                name="PodCrashLoopBackOff",
                fingerprint="fp-agent-accept",
                severity="critical",
                status="firing",
                service="message-service",
            )
        )
        run = await AgentRunRepository(session, "prod").create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                alert_event_id=alert.id,
                fault_service="message-service",
                anomaly_type="CrashLoopBackOff",
                confidence=90,
                root_cause_summary="pod crashed",
                action_type="pod_restart",
                target_resource="pod/message-0",
                risk_level="medium",
                node_states={},
                evidence_chain={"metrics": [], "logs": [], "traces": []},
            )
        )
        reject_run = await AgentRunRepository(session, "prod").create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                root_cause_summary="reject me",
                node_states={},
                evidence_chain={},
            )
        )
        await session.commit()
    headers = {
        "Authorization": f"Bearer {make_jwt(role='admin', sub='admin-1')}",
        "X-Project-Id": "prod",
    }

    accepted = await client.post(
        f"/api/v1/agent/suggestions/{run.id}/accept",
        headers=headers,
        json={"operatorNote": "同意执行"},
    )
    accepted_again = await client.post(
        f"/api/v1/agent/suggestions/{run.id}/accept",
        headers=headers,
        json={"operatorNote": "重复点击"},
    )
    rejected = await client.post(
        f"/api/v1/agent/suggestions/{reject_run.id}/reject",
        headers=headers,
        json={"reason": "业务高峰"},
    )

    assert accepted.status_code == 200
    assert accepted.json()["data"]["healingActionId"]
    assert accepted_again.json()["data"]["healingActionId"] == accepted.json()["data"]["healingActionId"]
    assert rejected.json()["data"] == {"success": True}
    async with app_instance.state.sessionmaker() as session:
        accepted_run = await AgentRunRepository(session, "prod").get(run.id)
        rejected_run = await AgentRunRepository(session, "prod").get(reject_run.id)
        actions = (
            await session.execute(text("SELECT * FROM heal_actions WHERE project_id='prod'"))
        ).all()
        action_rows = (await session.execute(select(HealAction))).scalars().all()
    assert accepted_run is not None
    assert rejected_run is not None
    assert accepted_run.node_states["suggestionStatus"] == "accepted"
    assert rejected_run.node_states["suggestionStatus"] == "rejected"
    assert len(actions) == 1
    assert len(action_rows) == 1


async def _async_noop(*args, **kwargs):
    return None


async def _async_true(*args, **kwargs):
    return True


async def _async_empty_dict(*args, **kwargs):
    return {}
