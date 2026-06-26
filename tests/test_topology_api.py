import json

import pytest
from sqlalchemy import text

from app.collectors.models import SpanSummary
from app.collectors.windows import MetricWindowStore, TraceCache
from app.core.constants import RedisKey
from app.core.projects import ProjectConfig, ProjectsConfig
from app.repositories.alerts import AlertEventCreate, AlertEventRepository
from app.topology.service import TopologyService


class WrongTypeListRedis:
    def __init__(self, entries):
        self.entries = entries

    async def get(self, key):
        raise RuntimeError("WRONGTYPE Operation against a key holding the wrong kind of value")

    async def lrange(self, key, start, end):
        return self.entries


@pytest.fixture(autouse=True)
async def _clean_topology_tables(app_instance):
    async with app_instance.state.sessionmaker() as session:
        await session.execute(text("TRUNCATE alert_events RESTART IDENTITY"))
        await session.commit()
    yield
    async with app_instance.state.sessionmaker() as session:
        await session.execute(text("TRUNCATE alert_events RESTART IDENTITY"))
        await session.commit()


@pytest.fixture
def topology_projects(app_instance):
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
    service: str = "message-service",
    fingerprint: str = "fp-message-service",
):
    async with app_instance.state.sessionmaker() as session:
        alert = await AlertEventRepository(session, project_id).create(
            AlertEventCreate(
                name="HighErrorRate",
                fingerprint=fingerprint,
                severity="critical",
                status="firing",
                service=service,
                namespace="prod-ns",
                pod=f"{service}-0",
                labels={"service": service},
                annotations={"summary": f"{service} has errors"},
            )
        )
        await session.commit()
        return alert.id


async def test_topology_graph_builds_edges_from_trace_cache(
    app_instance,
    client,
    make_jwt,
):
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={"prod": ProjectConfig(name="Prod", metric_profile="java")},
    )
    cache = TraceCache(max_traces_per_project=10)
    cache.put(
        "prod",
        "trace-1",
        [
            SpanSummary(
                traceId="trace-1",
                spanId="s1",
                parentSpanId=None,
                service="gateway",
                name="GET /messages",
                durationMs=20,
                status="success",
            ),
            SpanSummary(
                traceId="trace-1",
                spanId="s2",
                parentSpanId="s1",
                service="message-service",
                name="query",
                durationMs=120,
                status="error",
            ),
        ],
    )
    app_instance.state.trace_cache = cache
    headers = {
        "Authorization": f"Bearer {make_jwt(role='admin')}",
        "X-Project-Id": "prod",
    }

    response = await client.get("/api/v1/topology/graph", headers=headers)

    assert response.status_code == 200
    assert response.json()["data"]["edges"][0]["from"] == "gateway"
    assert response.json()["data"]["edges"][0]["to"] == "message-service"
    assert response.json()["data"]["edges"][0]["errorCount"] == 1


async def test_topology_graph_anomaly_filter_only_returns_error_edges_and_nodes(
    app_instance,
    client,
    auth_headers,
    topology_projects,
):
    cache = TraceCache(max_traces_per_project=10)
    cache.put(
        "prod",
        "trace-1",
        [
            SpanSummary(traceId="trace-1", spanId="s1", service="gateway"),
            SpanSummary(
                traceId="trace-1",
                spanId="s2",
                parentSpanId="s1",
                service="message-service",
                status="error",
            ),
            SpanSummary(
                traceId="trace-1",
                spanId="s3",
                parentSpanId="s1",
                service="database",
                status="success",
            ),
        ],
    )
    app_instance.state.trace_cache = cache

    response = await client.get(
        "/api/v1/topology/graph?filter=anomaly",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["edges"] == [
        {
            "from": "gateway",
            "to": "message-service",
            "count": 1,
            "errorCount": 1,
            "avgLatencyMs": 0.0,
            "status": "red",
        }
    ]
    assert {node["id"] for node in data["nodes"]} == {"gateway", "message-service"}
    assert all(node["status"] == "red" for node in data["nodes"])


async def test_topology_graph_caches_unfiltered_base_after_anomaly_request(
    app_instance,
    client,
    auth_headers,
    topology_projects,
):
    cache = TraceCache(max_traces_per_project=10)
    cache.put(
        "prod",
        "trace-1",
        [
            SpanSummary(traceId="trace-1", spanId="s1", service="gateway"),
            SpanSummary(
                traceId="trace-1",
                spanId="s2",
                parentSpanId="s1",
                service="message-service",
                status="error",
            ),
            SpanSummary(
                traceId="trace-1",
                spanId="s3",
                parentSpanId="s1",
                service="database",
                status="success",
            ),
        ],
    )
    app_instance.state.trace_cache = cache

    anomaly_response = await client.get(
        "/api/v1/topology/graph?filter=anomaly",
        headers=auth_headers,
    )
    all_response = await client.get("/api/v1/topology/graph", headers=auth_headers)

    assert anomaly_response.status_code == 200
    assert all_response.status_code == 200
    assert {
        (edge["from"], edge["to"], edge["status"])
        for edge in all_response.json()["data"]["edges"]
    } == {
        ("gateway", "database", "green"),
        ("gateway", "message-service", "red"),
    }

    raw_cached = await app_instance.state.redis.get(
        RedisKey.of("prod", RedisKey.TOPOLOGY_CACHE)
    )
    cached = json.loads(raw_cached)
    assert {
        (edge["from"], edge["to"], edge["status"]) for edge in cached["edges"]
    } == {
        ("gateway", "database", "green"),
        ("gateway", "message-service", "red"),
    }
    assert all("alertCount" not in node for node in cached["nodes"])


async def test_topology_graph_reads_cached_base_until_refresh(
    app_instance,
    client,
    auth_headers,
    topology_projects,
):
    cache = TraceCache(max_traces_per_project=10)
    cache.put(
        "prod",
        "trace-1",
        [
            SpanSummary(traceId="trace-1", spanId="s1", service="gateway"),
            SpanSummary(
                traceId="trace-1",
                spanId="s2",
                parentSpanId="s1",
                service="message-service",
            ),
        ],
    )
    app_instance.state.trace_cache = cache

    first_response = await client.get("/api/v1/topology/graph", headers=auth_headers)
    app_instance.state.trace_cache = TraceCache(max_traces_per_project=10)
    cached_response = await client.get("/api/v1/topology/graph", headers=auth_headers)
    await client.post("/api/v1/topology/refresh", headers=auth_headers)
    refreshed_response = await client.get("/api/v1/topology/graph", headers=auth_headers)

    assert first_response.status_code == 200
    assert cached_response.status_code == 200
    assert refreshed_response.status_code == 200
    assert cached_response.json()["data"]["edges"] == [
        {
            "from": "gateway",
            "to": "message-service",
            "count": 1,
            "errorCount": 0,
            "avgLatencyMs": 0.0,
            "status": "green",
        }
    ]
    assert refreshed_response.json()["data"]["edges"] == []


async def test_topology_graph_alert_filter_returns_edges_with_endpoint_nodes(
    app_instance,
    client,
    auth_headers,
    topology_projects,
):
    cache = TraceCache(max_traces_per_project=10)
    cache.put(
        "prod",
        "trace-1",
        [
            SpanSummary(traceId="trace-1", spanId="s1", service="gateway"),
            SpanSummary(
                traceId="trace-1",
                spanId="s2",
                parentSpanId="s1",
                service="message-service",
            ),
        ],
    )
    app_instance.state.trace_cache = cache
    await _seed_alert(app_instance, service="message-service")

    response = await client.get(
        "/api/v1/topology/graph?filter=alert",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["edges"] == [
        {
            "from": "gateway",
            "to": "message-service",
            "count": 1,
            "errorCount": 0,
            "avgLatencyMs": 0.0,
            "status": "green",
        }
    ]
    assert {node["id"] for node in data["nodes"]} == {"gateway", "message-service"}


async def test_topology_graph_uses_real_alert_count_per_service(
    app_instance,
    client,
    auth_headers,
    topology_projects,
):
    cache = TraceCache(max_traces_per_project=10)
    cache.put(
        "prod",
        "trace-1",
        [
            SpanSummary(traceId="trace-1", spanId="s1", service="gateway"),
            SpanSummary(
                traceId="trace-1",
                spanId="s2",
                parentSpanId="s1",
                service="message-service",
            ),
        ],
    )
    app_instance.state.trace_cache = cache
    await _seed_alert(
        app_instance,
        service="message-service",
        fingerprint="fp-message-service-1",
    )
    await _seed_alert(
        app_instance,
        service="message-service",
        fingerprint="fp-message-service-2",
    )

    response = await client.get("/api/v1/topology/graph", headers=auth_headers)

    assert response.status_code == 200
    node_by_id = {node["id"]: node for node in response.json()["data"]["nodes"]}
    assert node_by_id["message-service"]["status"] == "red"
    assert node_by_id["message-service"]["alertCount"] == 2


async def test_topology_service_detail_returns_node_upstream_downstream(
    app_instance,
    client,
    auth_headers,
    topology_projects,
):
    cache = TraceCache(max_traces_per_project=10)
    cache.put(
        "prod",
        "trace-1",
        [
            SpanSummary(traceId="trace-1", spanId="s1", service="gateway"),
            SpanSummary(
                traceId="trace-1",
                spanId="s2",
                parentSpanId="s1",
                service="message-service",
            ),
            SpanSummary(
                traceId="trace-1",
                spanId="s3",
                parentSpanId="s2",
                service="database",
            ),
        ],
    )
    app_instance.state.trace_cache = cache

    response = await client.get(
        "/api/v1/topology/services/message-service",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["node"]["id"] == "message-service"
    assert [edge["from"] for edge in data["upstream"]] == ["gateway"]
    assert [edge["to"] for edge in data["downstream"]] == ["database"]


async def test_topology_service_metrics_returns_window_placeholders(
    app_instance,
    client,
    auth_headers,
    topology_projects,
):
    store = MetricWindowStore()
    store.add("prod", "conn.active", 42.0)
    store.add("prod", "http.error_rate", 0.05)
    store.add("prod", "msg.p99_latency", 120.0)
    app_instance.state.metric_window_store = store

    response = await client.get(
        "/api/v1/topology/services/message-service/metrics",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "service": "message-service",
        "qps": 42.0,
        "errorRate": 0.05,
        "p99LatencyMs": 120.0,
    }


async def test_topology_service_alerts_are_project_scoped_and_filtered_by_service(
    app_instance,
    client,
    auth_headers,
    topology_projects,
):
    expected_id = await _seed_alert(
        app_instance,
        service="message-service",
        fingerprint="fp-prod-message",
    )
    await _seed_alert(app_instance, service="user-service", fingerprint="fp-prod-user")
    await _seed_alert(
        app_instance,
        project_id="stage",
        service="message-service",
        fingerprint="fp-stage-message",
    )

    response = await client.get(
        "/api/v1/topology/services/message-service/alerts",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["id"] == expected_id
    assert data["items"][0]["service"] == "message-service"


async def test_topology_service_logs_filters_recent_redis_errors_by_service(
    app_instance,
    client,
    auth_headers,
    topology_projects,
):
    key = RedisKey.of("prod", RedisKey.RECENT_ERRORS)
    await app_instance.state.redis.set(
        key,
        json.dumps(
            [
                {
                    "timestamp": "2026-06-17T09:00:00Z",
                    "level": "ERROR",
                    "service": "message-service",
                    "message": "database timeout",
                },
                {
                    "timestamp": "2026-06-17T09:00:01Z",
                    "level": "ERROR",
                    "service": "user-service",
                    "message": "ignored",
                },
            ]
        ),
    )

    response = await client.get(
        "/api/v1/topology/services/message-service/logs?page=1&pageSize=10",
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["page"] == 1
    assert data["pageSize"] == 10
    assert data["items"] == [
        {
            "timestamp": "2026-06-17T09:00:00Z",
            "level": "ERROR",
            "service": "message-service",
            "message": "database timeout",
        }
    ]


async def test_topology_service_logs_falls_back_to_lrange_when_get_wrongtype():
    redis = WrongTypeListRedis(
        [
            json.dumps(
                {
                    "timestamp": "2026-06-17T09:00:00Z",
                    "level": "ERROR",
                    "service": "message-service",
                    "message": "database timeout",
                }
            ).encode(),
            json.dumps(
                {
                    "timestamp": "2026-06-17T09:00:01Z",
                    "level": "ERROR",
                    "service": "user-service",
                    "message": "ignored",
                }
            ).encode(),
        ]
    )

    result = await TopologyService(redis=redis).logs_for_service(
        "prod",
        "message-service",
        page=1,
        page_size=10,
    )

    assert result == {
        "total": 1,
        "page": 1,
        "pageSize": 10,
        "items": [
            {
                "timestamp": "2026-06-17T09:00:00Z",
                "level": "ERROR",
                "service": "message-service",
                "message": "database timeout",
            }
        ],
    }


async def test_topology_refresh_deletes_cached_graph(
    app_instance,
    client,
    auth_headers,
    topology_projects,
):
    key = RedisKey.of("prod", RedisKey.TOPOLOGY_CACHE)
    await app_instance.state.redis.set(key, "cached")

    response = await client.post("/api/v1/topology/refresh", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["data"] == {"success": True}
    assert await app_instance.state.redis.get(key) is None


async def test_topology_rejects_unknown_project(client, make_jwt, topology_projects):
    response = await client.get(
        "/api/v1/topology/graph",
        headers={
            "Authorization": f"Bearer {make_jwt(role='admin')}",
            "X-Project-Id": "missing",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "项目不存在", "data": None}


async def test_topology_graph_rejects_disabled_project(client, make_jwt, topology_projects):
    response = await client.get(
        "/api/v1/topology/graph",
        headers={
            "Authorization": f"Bearer {make_jwt(role='admin')}",
            "X-Project-Id": "disabled",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "项目不存在", "data": None}


async def test_topology_graph_rebuilds_from_redis_traces(client, make_jwt, app_instance, topology_projects):
    from app.collectors.trace_store import save_trace_snapshot

    # 内存 trace_cache 空；topology cache 不预热；只写 Redis traces
    await save_trace_snapshot(
        app_instance.state.redis,
        "prod",
        [
            (
                "t1",
                [
                    SpanSummary(traceId="t1", spanId="s1", service="access-gateway", name="recv", durationMs=10),
                    SpanSummary(traceId="t1", spanId="s2", parentSpanId="s1", service="api-service", name="route", durationMs=20, status="error"),
                ],
            )
        ],
    )
    # 清掉可能存在的 topology 缓存，强制重建
    await app_instance.state.redis.delete(RedisKey.of("prod", RedisKey.TOPOLOGY_CACHE))

    headers = {"Authorization": f"Bearer {make_jwt(role='admin')}", "X-Project-Id": "prod"}
    resp = await client.get("/api/v1/topology/graph", headers=headers)
    data = resp.json()["data"]
    node_ids = {node["id"] for node in data["nodes"]}
    assert {"access-gateway", "api-service"} <= node_ids
    assert any(edge["from"] == "access-gateway" and edge["to"] == "api-service" for edge in data["edges"])
