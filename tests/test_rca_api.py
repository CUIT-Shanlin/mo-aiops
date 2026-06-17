from __future__ import annotations

from sqlalchemy import select, text

from app.core.projects import ProjectConfig, ProjectsConfig
from app.models.heal_action import HealAction
from app.repositories.agent_runs import AgentRunCreate, AgentRunRepository
from app.repositories.alerts import AlertEventCreate, AlertEventRepository


async def _clean_rca_tables(app_instance) -> None:
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "TRUNCATE heal_actions, alert_events, agent_runs "
                "RESTART IDENTITY"
            )
        )
        await session.commit()


async def _seed_run(
    app_instance,
    *,
    project_id: str = "prod",
    alert_event_id: int | None = None,
    node_states: dict | None = None,
    evidence_chain: dict | None = None,
    timeline: list[dict] | None = None,
    action_type: str | None = None,
    target_resource: str | None = None,
) -> int:
    async with app_instance.state.sessionmaker() as session:
        run = await AgentRunRepository(session, project_id).create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                alert_event_id=alert_event_id,
                root_cause_summary="JVM Heap 异常",
                fault_service="message-service",
                anomaly_type="HighMemory",
                severity="critical",
                confidence=0.92,
                action_type=action_type,
                target_resource=target_resource,
                node_states=node_states or {},
                evidence_chain=evidence_chain
                or {
                    "metrics": ["heap high"],
                    "logs": ["oom"],
                    "traces": ["slow span"],
                },
                timeline=timeline
                or [
                    {
                        "time": "10:15:30",
                        "event": "指标开始偏离动态基线",
                        "status": "completed",
                    }
                ],
            )
        )
        await session.commit()
        return int(run.id)


async def _seed_alert(
    app_instance,
    *,
    run_id: int | None = None,
    project_id: str = "prod",
    fingerprint: str = "fp-high-memory",
    namespace: str | None = "prod-ns",
    pod: str | None = "message-0",
) -> int:
    async with app_instance.state.sessionmaker() as session:
        alert = await AlertEventRepository(session, project_id).create(
            AlertEventCreate(
                name="HighMemory",
                fingerprint=fingerprint,
                severity="critical",
                status="firing",
                service="message-service",
                namespace=namespace,
                pod=pod,
                labels={"service": "message-service"},
                annotations={"summary": "heap high"},
                agent_run_id=run_id,
            )
        )
        await session.commit()
        return int(alert.id)


def _auth_headers(make_jwt, project_id: str = "prod") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {make_jwt(role='admin')}",
        "X-Project-Id": project_id,
    }


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


async def test_rca_detail_evidences_timeline_and_related_alerts(
    app_instance,
    client,
    make_jwt,
):
    await _clean_rca_tables(app_instance)
    _set_projects(app_instance)
    run_id = await _seed_run(
        app_instance,
        node_states={
            "recommended_actions": [{"step": 1, "description": "Pod 重启"}]
        },
    )
    alert_id = await _seed_alert(app_instance, run_id=run_id)

    detail = await client.get(
        f"/api/v1/rca/{run_id}",
        headers=_auth_headers(make_jwt),
    )
    evidences = await client.get(
        f"/api/v1/rca/{run_id}/evidences",
        headers=_auth_headers(make_jwt),
    )
    timeline = await client.get(
        f"/api/v1/rca/{run_id}/timeline",
        headers=_auth_headers(make_jwt),
    )
    related = await client.get(
        f"/api/v1/rca/{run_id}/related-alerts",
        headers=_auth_headers(make_jwt),
    )

    assert detail.status_code == 200
    assert detail.json()["code"] == 0
    assert detail.json()["data"]["rootCauseService"] == "message-service"
    assert evidences.json()["data"][0]["type"] == "metrics"
    assert timeline.json()["data"][0]["event"] == "指标开始偏离动态基线"
    assert related.json()["data"][0]["id"] == alert_id


async def test_rca_api_rejects_missing_unknown_and_disabled_project(
    app_instance,
    client,
    make_jwt,
):
    await _clean_rca_tables(app_instance)
    _set_projects(app_instance)
    run_id = await _seed_run(app_instance)
    auth = {"Authorization": f"Bearer {make_jwt(role='admin')}"}

    missing = await client.get(f"/api/v1/rca/{run_id}", headers=auth)
    unknown = await client.get(
        f"/api/v1/rca/{run_id}",
        headers={**auth, "X-Project-Id": "unknown"},
    )
    disabled = await client.get(
        f"/api/v1/rca/{run_id}",
        headers={**auth, "X-Project-Id": "disabled"},
    )

    for response in (missing, unknown, disabled):
        body = response.json()
        assert body["code"] == 40004 or body["message"] == "项目不存在"


async def test_rca_cross_project_agent_run_is_not_visible(
    app_instance,
    client,
    make_jwt,
):
    await _clean_rca_tables(app_instance)
    _set_projects(app_instance)
    run_id = await _seed_run(app_instance, project_id="stage")

    response = await client.get(
        f"/api/v1/rca/{run_id}",
        headers=_auth_headers(make_jwt, "prod"),
    )

    assert response.status_code == 200
    assert response.json()["code"] == 40004
    assert response.json()["message"] == "RCA 不存在"


async def test_rca_full_evidence_returns_metrics_logs_and_traces(
    app_instance,
    client,
    make_jwt,
):
    await _clean_rca_tables(app_instance)
    _set_projects(app_instance)
    run_id = await _seed_run(
        app_instance,
        evidence_chain={"metrics": ["heap high"], "logs": ["oom"]},
    )

    response = await client.get(
        f"/api/v1/rca/{run_id}/full-evidence",
        headers=_auth_headers(make_jwt),
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"] == {
        "metrics": ["heap high"],
        "logs": ["oom"],
        "traces": [],
    }


async def test_rca_execute_fix_creates_selected_healing_actions(
    app_instance,
    client,
    make_jwt,
):
    await _clean_rca_tables(app_instance)
    _set_projects(app_instance)
    run_id = await _seed_run(
        app_instance,
        evidence_chain={
            "metrics": ["heap high"],
            "logs": ["oom"],
            "traces": ["slow span"],
            "recommended_actions": [
                {"step": 1, "description": "Pod 重启 message-service"},
                {"step": 2, "description": "配置 HPA 扩容"},
                {"step": 3, "description": "灰度发布恢复"},
            ],
        },
    )
    await _seed_alert(app_instance, run_id=run_id)

    response = await client.post(
        f"/api/v1/rca/{run_id}/execute-fix",
        headers=_auth_headers(make_jwt),
        json={"steps": [1, 2]},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0
    action_ids = response.json()["data"]["healingActionIds"]
    assert len(action_ids) == 2
    assert all(isinstance(action_id, str) for action_id in action_ids)

    async with app_instance.state.sessionmaker() as session:
        rows = (
            await session.execute(
                select(HealAction).order_by(HealAction.id.asc())
            )
        ).scalars().all()

    assert [row.action_type for row in rows] == ["pod_restart", "hpa_scale"]
    assert [row.status for row in rows] == ["pending", "pending"]


async def test_rca_execute_fix_ignores_action_type_and_risk_overrides(
    app_instance,
    client,
    make_jwt,
):
    await _clean_rca_tables(app_instance)
    _set_projects(app_instance)
    run_id = await _seed_run(
        app_instance,
        evidence_chain={
            "metrics": ["heap high"],
            "logs": ["oom"],
            "traces": ["slow span"],
            "recommended_actions": [
                {
                    "step": 1,
                    "description": "配置 HPA 扩容",
                    "actionType": "pod_restart",
                    "riskLevel": "high",
                },
                {
                    "step": 2,
                    "description": "灰度发布恢复",
                    "actionType": "hpa_scale",
                    "riskLevel": "medium",
                },
            ],
        },
    )
    await _seed_alert(app_instance, run_id=run_id)

    response = await client.post(
        f"/api/v1/rca/{run_id}/execute-fix",
        headers=_auth_headers(make_jwt),
        json={"steps": [1, 2]},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0

    async with app_instance.state.sessionmaker() as session:
        rows = (
            await session.execute(
                select(HealAction).order_by(HealAction.id.asc())
            )
        ).scalars().all()

    assert [(row.action_type, row.risk_level) for row in rows] == [
        ("hpa_scale", "medium"),
        ("rolling_restart", "high"),
    ]


async def test_rca_related_alerts_supports_run_alert_event_id_and_execute_fix_target(
    app_instance,
    client,
    make_jwt,
):
    await _clean_rca_tables(app_instance)
    _set_projects(app_instance)
    alert_id = await _seed_alert(
        app_instance,
        run_id=None,
        fingerprint="fp-primary-alert",
        namespace="prod-ns",
        pod="message-7",
    )
    run_id = await _seed_run(
        app_instance,
        alert_event_id=alert_id,
        evidence_chain={
            "metrics": ["heap high"],
            "logs": ["oom"],
            "traces": ["slow span"],
            "recommended_actions": [
                {"step": 1, "description": "Pod 重启"},
            ],
        },
    )

    related = await client.get(
        f"/api/v1/rca/{run_id}/related-alerts",
        headers=_auth_headers(make_jwt),
    )
    execute = await client.post(
        f"/api/v1/rca/{run_id}/execute-fix",
        headers=_auth_headers(make_jwt),
        json={"steps": [1]},
    )

    assert related.status_code == 200
    assert related.json()["code"] == 0
    assert related.json()["data"][0]["id"] == alert_id
    assert execute.status_code == 200
    assert execute.json()["code"] == 0

    async with app_instance.state.sessionmaker() as session:
        rows = (
            await session.execute(
                select(HealAction).order_by(HealAction.id.asc())
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].alert_event_id == alert_id
    assert rows[0].project_id == "prod"
    assert rows[0].target_namespace == "prod-ns"
    assert rows[0].target_resource == "message-7"


async def test_rca_execute_fix_rejects_multiple_alerts_without_primary_alert(
    app_instance,
    client,
    make_jwt,
):
    await _clean_rca_tables(app_instance)
    _set_projects(app_instance)
    run_id = await _seed_run(
        app_instance,
        evidence_chain={
            "metrics": ["heap high"],
            "logs": ["oom"],
            "traces": ["slow span"],
            "recommended_actions": [{"step": 1, "description": "Pod 重启"}],
        },
    )
    await _seed_alert(app_instance, run_id=run_id, fingerprint="fp-related-1")
    await _seed_alert(app_instance, run_id=run_id, fingerprint="fp-related-2")

    response = await client.post(
        f"/api/v1/rca/{run_id}/execute-fix",
        headers=_auth_headers(make_jwt),
        json={"steps": [1]},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 40022

    async with app_instance.state.sessionmaker() as session:
        rows = (await session.execute(select(HealAction))).scalars().all()

    assert rows == []


async def test_rca_execute_fix_rejects_duplicate_steps_without_creating_actions(
    app_instance,
    client,
    make_jwt,
):
    await _clean_rca_tables(app_instance)
    _set_projects(app_instance)
    run_id = await _seed_run(
        app_instance,
        evidence_chain={
            "metrics": ["heap high"],
            "logs": ["oom"],
            "traces": ["slow span"],
            "recommended_actions": [{"step": 1, "description": "配置 HPA 扩容"}],
        },
    )
    await _seed_alert(app_instance, run_id=run_id)

    response = await client.post(
        f"/api/v1/rca/{run_id}/execute-fix",
        headers=_auth_headers(make_jwt),
        json={"steps": [1, 1]},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 40022

    async with app_instance.state.sessionmaker() as session:
        rows = (await session.execute(select(HealAction))).scalars().all()

    assert rows == []


async def test_rca_execute_fix_rejects_duplicate_mapped_action_types(
    app_instance,
    client,
    make_jwt,
):
    await _clean_rca_tables(app_instance)
    _set_projects(app_instance)
    run_id = await _seed_run(
        app_instance,
        evidence_chain={
            "metrics": ["heap high"],
            "logs": ["oom"],
            "traces": ["slow span"],
            "recommended_actions": [
                {"step": 1, "description": "Pod 重启 message-0"},
                {"step": 2, "description": "restart message-1"},
            ],
        },
    )
    await _seed_alert(app_instance, run_id=run_id)

    response = await client.post(
        f"/api/v1/rca/{run_id}/execute-fix",
        headers=_auth_headers(make_jwt),
        json={"steps": [1, 2]},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 40022

    async with app_instance.state.sessionmaker() as session:
        rows = (await session.execute(select(HealAction))).scalars().all()

    assert rows == []
