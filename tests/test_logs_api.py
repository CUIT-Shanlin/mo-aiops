import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.constants import RedisKey
from app.core.projects import ProjectConfig, ProjectsConfig
from app.logs.service import LogFilters, LogsService


SAMPLE_LOGS = [
    {
        "timestamp": "2026-06-17T09:00:00Z",
        "level": "ERROR",
        "service": "message-service",
        "namespace": "prod",
        "pod": "message-0",
        "traceId": "trace-1",
        "spanId": "span-1",
        "message": "OutOfMemoryError heap",
    },
    {
        "timestamp": "2026-06-17T09:05:00Z",
        "level": "WARN",
        "service": "gateway",
        "traceId": "trace-2",
        "message": "slow request",
    },
    {
        "timestamp": "2026-06-17T09:10:00Z",
        "level": "INFO",
        "service": "message-service",
        "traceId": "trace-3",
        "message": "ok",
    },
]


class FakeRedis:
    def __init__(
        self,
        *,
        get_value=None,
        list_values: list | None = None,
        get_error: Exception | None = None,
    ):
        self.get_value = get_value
        self.list_values = list_values or []
        self.get_error = get_error
        self.lrange_calls = 0

    async def get(self, key):
        if self.get_error is not None:
            raise self.get_error
        return self.get_value

    async def lrange(self, key, start, end):
        self.lrange_calls += 1
        return self.list_values


@pytest.fixture(autouse=True)
async def _clean_saved_queries(app_instance):
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "DO $$ BEGIN "
                "IF to_regclass('public.saved_queries') IS NOT NULL THEN "
                "TRUNCATE saved_queries RESTART IDENTITY; "
                "END IF; "
                "END $$;"
            )
        )
        await session.commit()
    yield
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "DO $$ BEGIN "
                "IF to_regclass('public.saved_queries') IS NOT NULL THEN "
                "TRUNCATE saved_queries RESTART IDENTITY; "
                "END IF; "
                "END $$;"
            )
        )
        await session.commit()


@pytest.fixture
def logs_projects_config(app_instance):
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
            "disabled": ProjectConfig(
                name="Disabled", metric_profile="java", enabled=False
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


async def _seed_recent_errors(redis, project_id: str, logs: list[dict]) -> None:
    await redis.set(
        RedisKey.of(project_id, RedisKey.RECENT_ERRORS),
        json.dumps(logs),
        ex=600,
    )


async def test_logs_search_reads_project_recent_errors_filters_counts_and_pages(
    app_instance,
    client,
    auth_headers,
    logs_projects_config,
):
    await _seed_recent_errors(app_instance.state.redis, "prod", SAMPLE_LOGS)
    await _seed_recent_errors(
        app_instance.state.redis,
        "stage",
        [
            {
                "timestamp": "2026-06-17T09:00:00Z",
                "level": "ERROR",
                "service": "stage-only",
                "traceId": "trace-stage",
                "message": "OutOfMemoryError heap",
            }
        ],
    )

    response = await client.get(
        "/api/v1/logs/search?keyword=trace-&service=message-service&page=1&pageSize=1",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    page = body["data"]
    assert page["total"] == 2
    assert page["errorCount"] == 1
    assert page["warnCount"] == 0
    assert page["infoCount"] == 1
    assert page["debugCount"] == 0
    assert page["page"] == 1
    assert page["pageSize"] == 1
    assert len(page["items"]) == 1
    assert page["items"][0]["service"] == "message-service"
    assert page["items"][0]["traceId"] != "trace-stage"

    second_page = await client.get(
        "/api/v1/logs/search?keyword=trace-&service=message-service&page=2&pageSize=1",
        headers=auth_headers,
    )
    assert second_page.json()["data"]["page"] == 2
    assert len(second_page.json()["data"]["items"]) == 1


async def test_logs_search_filters_level_and_trace_id(
    app_instance,
    client,
    auth_headers,
    logs_projects_config,
):
    await _seed_recent_errors(app_instance.state.redis, "prod", SAMPLE_LOGS)

    response = await client.get(
        "/api/v1/logs/search?level=error&traceId=trace-1",
        headers=auth_headers,
    )

    assert response.status_code == 200
    page = response.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["message"] == "OutOfMemoryError heap"


async def test_logs_histogram_buckets_filtered_logs(
    app_instance,
    client,
    auth_headers,
    logs_projects_config,
):
    await _seed_recent_errors(app_instance.state.redis, "prod", SAMPLE_LOGS)

    response = await client.get(
        "/api/v1/logs/histogram?service=message-service&buckets=2",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    buckets = body["data"]
    assert len(buckets) == 2
    assert sum(bucket["count"] for bucket in buckets) == 2
    assert sum(bucket["errorCount"] for bucket in buckets) == 1
    assert all("time" in bucket for bucket in buckets)


async def test_logs_histogram_uses_explicit_window_for_bucket_axis(
    app_instance,
    client,
    auth_headers,
    logs_projects_config,
):
    await _seed_recent_errors(app_instance.state.redis, "prod", SAMPLE_LOGS)

    response = await client.get(
        "/api/v1/logs/histogram"
        "?service=message-service"
        "&startTime=2026-06-17T08:00:00Z"
        "&endTime=2026-06-17T10:00:00Z"
        "&buckets=4",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["data"] == [
        {"time": "2026-06-17T08:00:00Z", "count": 0, "errorCount": 0},
        {"time": "2026-06-17T08:30:00Z", "count": 0, "errorCount": 0},
        {"time": "2026-06-17T09:00:00Z", "count": 2, "errorCount": 1},
        {"time": "2026-06-17T09:30:00Z", "count": 0, "errorCount": 0},
    ]


async def test_logs_services_returns_distinct_services_from_redis_list(
    app_instance,
    client,
    auth_headers,
    logs_projects_config,
):
    key = RedisKey.of("prod", RedisKey.RECENT_ERRORS)
    await app_instance.state.redis.rpush(
        key,
        *[json.dumps(log) for log in reversed(SAMPLE_LOGS)],
    )

    response = await client.get("/api/v1/logs/services", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": ["gateway", "message-service"],
    }


async def test_logs_saved_queries_are_project_scoped(
    client,
    auth_headers,
    make_jwt,
    logs_projects_config,
):
    stage_headers = {
        "Authorization": f"Bearer {make_jwt(role='admin')}",
        "X-Project-Id": "stage",
    }

    prod_save = await client.post(
        "/api/v1/logs/queries/save",
        headers=auth_headers,
        json={
            "name": "OOM",
            "params": {"keyword": "OutOfMemoryError", "service": "message-service"},
        },
    )
    stage_save = await client.post(
        "/api/v1/logs/queries/save",
        headers=stage_headers,
        json={"name": "Stage only", "params": {"service": "stage-only"}},
    )

    assert prod_save.status_code == 200
    assert prod_save.json()["code"] == 0
    assert isinstance(prod_save.json()["data"]["id"], str)
    assert stage_save.status_code == 200
    assert stage_save.json()["code"] == 0

    response = await client.get("/api/v1/logs/queries", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"] == [
        {
            "id": prod_save.json()["data"]["id"],
            "name": "OOM",
            "params": {
                "keyword": "OutOfMemoryError",
                "service": "message-service",
            },
            "createdAt": response.json()["data"][0]["createdAt"],
        }
    ]


async def test_logs_saved_query_duplicate_name_rejected_per_project(
    client,
    auth_headers,
    make_jwt,
    logs_projects_config,
):
    stage_headers = {
        "Authorization": f"Bearer {make_jwt(role='admin')}",
        "X-Project-Id": "stage",
    }

    first = await client.post(
        "/api/v1/logs/queries/save",
        headers=auth_headers,
        json={"name": "OOM", "params": {"keyword": "oom"}},
    )
    duplicate = await client.post(
        "/api/v1/logs/queries/save",
        headers=auth_headers,
        json={"name": "OOM", "params": {"keyword": "heap"}},
    )
    stage_same_name = await client.post(
        "/api/v1/logs/queries/save",
        headers=stage_headers,
        json={"name": "OOM", "params": {"keyword": "stage"}},
    )

    assert first.json()["code"] == 0
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "code": 40022,
        "message": "查询名称已存在",
        "data": None,
    }
    assert stage_same_name.json()["code"] == 0

    prod_list = await client.get("/api/v1/logs/queries", headers=auth_headers)
    assert [item["name"] for item in prod_list.json()["data"]] == ["OOM"]


async def test_logs_service_does_not_fallback_to_lrange_when_get_has_data():
    fake_redis = FakeRedis(
        get_value=json.dumps(SAMPLE_LOGS[:1]),
        list_values=[json.dumps(SAMPLE_LOGS[1])],
    )

    result = await LogsService(fake_redis).search("prod", LogFilters())

    assert result["total"] == 1
    assert result["items"][0]["traceId"] == "trace-1"
    assert fake_redis.lrange_calls == 0


async def test_logs_service_falls_back_to_lrange_when_get_fails():
    fake_redis = FakeRedis(
        get_error=RuntimeError("WRONGTYPE"),
        list_values=[json.dumps(log) for log in SAMPLE_LOGS[:2]],
    )

    search = await LogsService(fake_redis).search("prod", LogFilters())
    histogram = await LogsService(fake_redis).histogram(
        "prod", LogFilters(), buckets=2
    )

    assert search["total"] == 2
    assert sum(bucket["count"] for bucket in histogram) == 2
    assert fake_redis.lrange_calls == 2


async def test_logs_histogram_explicit_empty_window_returns_timed_empty_buckets(
    client,
    auth_headers,
    logs_projects_config,
):
    response = await client.get(
        "/api/v1/logs/histogram"
        "?startTime=2026-06-17T08:00:00Z"
        "&endTime=2026-06-17T09:00:00Z"
        "&buckets=2",
        headers=auth_headers,
    )

    assert response.status_code == 200
    buckets = response.json()["data"]
    assert buckets == [
        {"time": "2026-06-17T08:00:00Z", "count": 0, "errorCount": 0},
        {"time": "2026-06-17T08:30:00Z", "count": 0, "errorCount": 0},
    ]


async def test_logs_histogram_defaults_to_recent_one_hour_when_no_window_or_logs(
    monkeypatch,
):
    service = LogsService(FakeRedis(get_value=json.dumps([])))
    fixed_now = datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(service, "_now", lambda: fixed_now)

    buckets = await service.histogram("prod", LogFilters(), buckets=4)

    assert buckets == [
        {"time": "2026-06-17T09:00:00Z", "count": 0, "errorCount": 0},
        {"time": "2026-06-17T09:15:00Z", "count": 0, "errorCount": 0},
        {"time": "2026-06-17T09:30:00Z", "count": 0, "errorCount": 0},
        {"time": "2026-06-17T09:45:00Z", "count": 0, "errorCount": 0},
    ]


async def test_logs_api_rejects_disabled_project(
    client,
    make_jwt,
    logs_projects_config,
):
    response = await client.get(
        "/api/v1/logs/search",
        headers={
            "Authorization": f"Bearer {make_jwt(role='admin')}",
            "X-Project-Id": "disabled",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "项目不存在", "data": None}
