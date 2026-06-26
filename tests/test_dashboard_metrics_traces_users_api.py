from __future__ import annotations

from sqlalchemy import select, text

from app.collectors.models import SpanSummary
from app.core.projects import ProjectConfig, ProjectsConfig
from app.models.audit_log import AuditLog
from app.repositories.users import UserCreate, UserRepository


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
                "TRUNCATE users, audit_logs, heal_actions, agent_runs, alert_events "
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


async def test_metric_categories_value_keeps_two_decimals(
    client, make_jwt, app_instance
):
    await _clean(app_instance)
    _set_projects(app_instance)
    app_instance.state.metric_window_store.add("prod", "sys.cpu", 0.0761296)
    app_instance.state.metric_window_store.add("prod", "sys.memory", 75.235641)
    app_instance.state.metric_window_store.add("prod", "msg.throughput", 12600)
    headers = _headers(make_jwt(role="admin"))

    categories = await client.get("/api/v1/metrics/categories", headers=headers)

    values = {
        metric["name"]: metric["value"]
        for category in categories.json()["data"]
        for metric in category["metrics"]
    }
    assert values["CPU 使用率"] == "0.08"
    assert values["内存使用率"] == "75.24"
    # 整数值不带多余小数
    assert values["消息 TPS"] == "12600"


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


async def test_metrics_and_traces_accept_frontend_time_query_params(
    client,
    make_jwt,
    app_instance,
):
    _set_projects(app_instance)
    app_instance.state.metric_window_store.add("prod", "msg.throughput", 12600)
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
            )
        ],
    )
    headers = _headers(make_jwt(role="admin"))

    metric_series = await client.get(
        "/api/v1/metrics/msg.throughput/series?timeRange=1h&step=1m",
        headers=headers,
    )
    traces = await client.get("/api/v1/traces?timeRange=1h", headers=headers)

    assert metric_series.status_code == 200
    assert metric_series.json()["code"] == 0
    assert metric_series.json()["data"][0]["value"] == 12600
    assert traces.status_code == 200
    assert traces.json()["code"] == 0
    assert traces.json()["data"]["items"][0]["traceId"] == "trace-prod"

    openapi = (await client.get("/openapi.json")).json()
    metrics_parameters = {
        parameter["name"]
        for parameter in openapi["paths"]["/api/v1/metrics/{name}/series"]["get"][
            "parameters"
        ]
    }
    traces_parameters = {
        parameter["name"]
        for parameter in openapi["paths"]["/api/v1/traces"]["get"]["parameters"]
    }
    assert {"timeRange", "step"} <= metrics_parameters
    assert "timeRange" in traces_parameters


async def test_users_ban_and_unban_validate_body_and_write_audit(
    client,
    make_jwt,
    app_instance,
):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin-1"))
    await client.get("/api/v1/users", headers=headers)

    missing = await client.post(
        "/api/v1/users/admin/ban",
        headers=headers,
        json={"reason": "spam"},
    )
    banned = await client.post(
        "/api/v1/users/admin/ban",
        headers=headers,
        json={
            "reason": "spam",
            "duration": "1h",
            "forceDisconnect": True,
        },
    )
    unbanned = await client.post(
        "/api/v1/users/admin/unban",
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
        "userId": "admin",
        "duration": "1h",
        "forceDisconnect": True,
    }


async def test_users_missing_ban_unban_return_not_found(
    client,
    make_jwt,
    app_instance,
):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin-1"))
    await client.get("/api/v1/users", headers=headers)

    banned = await client.post(
        "/api/v1/users/missing/ban",
        headers=headers,
        json={"reason": "missing", "duration": "1h", "forceDisconnect": False},
    )
    unbanned = await client.post(
        "/api/v1/users/missing/unban",
        headers=headers,
        json={"reason": "missing"},
    )

    assert banned.json() == {"code": 40004, "message": "用户不存在", "data": None}
    assert unbanned.json() == {"code": 40004, "message": "用户不存在", "data": None}
    async with app_instance.state.sessionmaker() as session:
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.project_id == "prod").order_by(AuditLog.id)
            )
        ).scalars().all()
    assert [audit.action for audit in audits] == []


async def test_users_list_stats_detail_use_local_users_table(
    client,
    make_jwt,
    app_instance,
):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin"))

    stats = await client.get("/api/v1/users/stats", headers=headers)
    users = await client.get("/api/v1/users", headers=headers)
    detail = await client.get("/api/v1/users/admin", headers=headers)

    assert stats.json()["data"] == {
        "total": 1,
        "online": 0,
        "banned": 0,
        "highRisk": 0,
        "riskUsers": 0,
    }
    assert users.json()["data"]["total"] == 1
    assert users.json()["data"]["items"][0] == {
        "id": "admin",
        "username": "admin",
        "isBanned": False,
        "onlineStatus": "offline",
        "riskLevel": "normal",
        "todayMessages": 0,
        "lastLogin": None,
        "registeredAt": users.json()["data"]["items"][0]["registeredAt"],
        "avatar": None,
        "email": None,
    }
    assert detail.json()["data"]["id"] == "admin"
    assert detail.json()["data"]["onlineStatus"] == "offline"
    assert detail.json()["data"]["riskLevel"] == "normal"


async def test_traces_routes_fallback_to_redis_when_memory_empty(client, make_jwt, app_instance):
    from app.collectors.models import SpanSummary
    from app.collectors.trace_store import save_trace_snapshot

    _set_projects(app_instance)
    # 内存 trace_cache 保持空，只写 Redis
    await save_trace_snapshot(
        app_instance.state.redis,
        "prod",
        [
            (
                "redis-trace",
                [
                    SpanSummary(traceId="redis-trace", spanId="s1", service="access-gateway", name="recv", durationMs=10),
                    SpanSummary(traceId="redis-trace", spanId="s2", parentSpanId="s1", service="api-service", name="route", durationMs=20, status="error"),
                ],
            )
        ],
    )
    headers = _headers(make_jwt(role="admin"))

    traces = await client.get("/api/v1/traces", headers=headers)
    detail = await client.get("/api/v1/traces/redis-trace", headers=headers)
    services = await client.get("/api/v1/traces/services", headers=headers)

    assert traces.json()["data"]["total"] == 1
    assert traces.json()["data"]["items"][0]["traceId"] == "redis-trace"
    assert detail.json()["data"]["traceId"] == "redis-trace"
    assert services.json()["data"] == ["access-gateway", "api-service"]


async def test_users_filters_and_ban_unban_update_local_user(
    client,
    make_jwt,
    app_instance,
):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin"))
    await client.get("/api/v1/users", headers=headers)
    async with app_instance.state.sessionmaker() as session:
        repo = UserRepository(session, "prod")
        await repo.create(
            UserCreate(
                id="alice",
                username="alice",
                online_status="offline",
                risk_level="normal",
            )
        )
        await repo.create(
            UserCreate(
                id="bob",
                username="bob",
                online_status="online",
                risk_level="high",
            )
        )
        await session.commit()

    missing_filter = await client.get(
        "/api/v1/users",
        params={"keyword": "missing"},
        headers=headers,
    )
    banned = await client.post(
        "/api/v1/users/admin/ban",
        headers=headers,
        json={"reason": "abuse", "duration": "1h", "forceDisconnect": True},
    )
    banned_filter = await client.get(
        "/api/v1/users",
        params={"isBanned": True},
        headers=headers,
    )
    filtered_page = await client.get(
        "/api/v1/users",
        params={
            "onlineStatus": "offline",
            "riskLevel": "normal",
            "page": 1,
            "pageSize": 1,
        },
        headers=headers,
    )
    unbanned = await client.post(
        "/api/v1/users/admin/unban",
        headers=headers,
        json={"reason": "appeal accepted"},
    )
    detail = await client.get("/api/v1/users/admin", headers=headers)

    assert missing_filter.json()["data"]["total"] == 0
    assert banned.json()["data"] == {"success": True, "forceDisconnect": True}
    assert banned_filter.json()["data"]["total"] == 1
    assert banned_filter.json()["data"]["items"][0]["isBanned"] is True
    assert filtered_page.json()["data"]["page"] == 1
    assert filtered_page.json()["data"]["pageSize"] == 1
    assert filtered_page.json()["data"]["total"] == 2
    assert len(filtered_page.json()["data"]["items"]) == 1
    assert filtered_page.json()["data"]["items"][0]["id"] == "admin"
    assert unbanned.json()["data"] == {"success": True}
    assert detail.json()["data"]["isBanned"] is False
