from __future__ import annotations

from sqlalchemy import select, text

from app.collectors.models import SpanSummary
from app.core.projects import ProjectConfig, ProjectsConfig
from app.models.audit_log import AuditLog


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


def _headers(token: str, project_id: str = "prod") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Project-Id": project_id}


async def _clean(app_instance) -> None:
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "TRUNCATE audit_logs, heal_actions, agent_runs, alert_events "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()


def test_dashboard_metrics_traces_users_routes_are_registered():
    from app.main import get_app

    routes = {getattr(route, "path", "") for route in get_app().routes}

    assert {
        "/api/v1/dashboard/stats",
        "/api/v1/dashboard/health-score",
        "/api/v1/dashboard/metrics/series",
        "/api/v1/dashboard/alerts/recent",
        "/api/v1/dashboard/events",
        "/api/v1/dashboard/ai-agent/status",
        "/api/v1/dashboard/service-chain",
        "/api/v1/metrics/categories",
        "/api/v1/metrics/{name}/series",
        "/api/v1/traces",
        "/api/v1/traces/{trace_id}",
        "/api/v1/traces/{trace_id}/spans",
        "/api/v1/traces/services",
        "/api/v1/users/stats",
        "/api/v1/users",
        "/api/v1/users/{user_id}",
        "/api/v1/users/{user_id}/ban",
        "/api/v1/users/{user_id}/unban",
    } <= routes


async def test_dashboard_routes_require_project_context(client, make_jwt, app_instance):
    _set_projects(app_instance)
    response = await client.get(
        "/api/v1/dashboard/stats",
        headers={"Authorization": f"Bearer {make_jwt(role='admin')}"},
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "项目不存在", "data": None}


async def test_dashboard_and_metrics_return_mvp_shapes(client, make_jwt, app_instance):
    await _clean(app_instance)
    _set_projects(app_instance)
    app_instance.state.metric_window_store.add("prod", "msg.throughput", 12600)
    app_instance.state.metric_window_store.add("prod", "runtime.memory_used_ratio", 68)
    headers = _headers(make_jwt(role="admin"))

    stats = await client.get("/api/v1/dashboard/stats", headers=headers)
    health = await client.get("/api/v1/dashboard/health-score", headers=headers)
    series = await client.get("/api/v1/dashboard/metrics/series", headers=headers)
    categories = await client.get("/api/v1/metrics/categories", headers=headers)
    metric_series = await client.get(
        "/api/v1/metrics/msg.throughput/series",
        headers=headers,
    )

    assert stats.status_code == 200
    assert stats.json()["code"] == 0
    assert set(stats.json()["data"]) >= {
        "alertCount",
        "criticalCount",
        "recoveredCount",
        "healingSuccessRate",
        "tcpConnections",
        "messageTps",
        "mqBacklog",
        "p99Latency",
    }
    assert health.json()["data"]["score"] <= 100
    assert isinstance(health.json()["data"]["trend"], list)
    assert {item["title"] for item in series.json()["data"]} >= {
        "消息吞吐量 (msg/s)",
        "JVM Heap 使用率 (%)",
    }
    assert categories.json()["data"][0]["category"] == "系统资源"
    assert metric_series.json()["data"][0].keys() >= {"time", "value", "baseline"}


async def test_traces_routes_read_project_trace_cache(client, make_jwt, app_instance):
    _set_projects(app_instance)
    app_instance.state.trace_cache.put(
        "prod",
        "trace-prod",
        [
            SpanSummary(
                traceId="trace-prod",
                spanId="s1",
                service="gateway",
                name="gateway.receive",
                durationMs=10,
                status="success",
            ),
            SpanSummary(
                traceId="trace-prod",
                spanId="s2",
                parentSpanId="s1",
                service="message-service",
                name="message.process",
                durationMs=280,
                status="error",
            ),
        ],
    )
    app_instance.state.trace_cache.put(
        "stage",
        "trace-stage",
        [
            SpanSummary(
                traceId="trace-stage",
                spanId="s1",
                service="stage-service",
                name="stage",
            )
        ],
    )
    headers = _headers(make_jwt(role="admin"))

    traces = await client.get("/api/v1/traces", headers=headers)
    detail = await client.get("/api/v1/traces/trace-prod", headers=headers)
    spans = await client.get("/api/v1/traces/trace-prod/spans", headers=headers)
    services = await client.get("/api/v1/traces/services", headers=headers)

    assert traces.json()["data"]["total"] == 1
    assert traces.json()["data"]["items"][0]["traceId"] == "trace-prod"
    assert detail.json()["data"]["traceId"] == "trace-prod"
    assert spans.json()["data"][1]["startOffset"] == 10
    assert services.json()["data"] == ["gateway", "message-service"]


async def test_users_ban_and_unban_validate_body_and_write_audit(
    client,
    make_jwt,
    app_instance,
):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin-1"))

    missing = await client.post(
        "/api/v1/users/u1/ban",
        headers=headers,
        json={"reason": "spam"},
    )
    banned = await client.post(
        "/api/v1/users/u1/ban",
        headers=headers,
        json={
            "reason": "spam",
            "duration": "1h",
            "forceDisconnect": True,
        },
    )
    unbanned = await client.post(
        "/api/v1/users/u1/unban",
        headers=headers,
        json={"reason": "appeal accepted"},
    )

    assert missing.status_code == 200
    assert missing.json()["code"] == 40022
    assert banned.json()["data"] == {"success": True, "forceDisconnect": True}
    assert unbanned.json()["data"] == {"success": True}
    async with app_instance.state.sessionmaker() as session:
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.project_id == "prod").order_by(AuditLog.id)
            )
        ).scalars().all()
    assert [audit.action for audit in audits] == ["USER_BAN", "USER_UNBAN"]
    assert audits[0].after_state == {
        "userId": "u1",
        "duration": "1h",
        "forceDisconnect": True,
    }
