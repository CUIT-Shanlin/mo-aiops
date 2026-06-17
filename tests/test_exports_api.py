import json
from io import StringIO

import csv

import pytest
from sqlalchemy import text

from app.core.constants import RedisKey
from app.core.projects import ProjectConfig, ProjectsConfig
from app.exports.service import EXPORT_TTL_SECONDS, ExportStore
from app.models.agent_run import AgentRun
from app.models.audit_log import AuditLog


class FakeRedis:
    def __init__(self, row):
        self.row = row

    async def hgetall(self, key):
        return self.row


@pytest.fixture(autouse=True)
async def _clean_export_tables(app_instance):
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "TRUNCATE audit_logs, heal_actions, alert_events, agent_runs "
                "RESTART IDENTITY"
            )
        )
        await session.commit()
    yield


@pytest.fixture
def export_projects_config(app_instance):
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


async def test_export_download_returns_project_scoped_artifact(
    app_instance, client, auth_headers, export_projects_config
):
    await app_instance.state.redis.hset(
        RedisKey.of("prod", "exports:artifact-1"),
        mapping={
            "content": "a,b\n1,2\n",
            "filename": "report.csv",
            "content_type": "text/csv; charset=utf-8",
        },
    )
    await app_instance.state.redis.hset(
        RedisKey.of("stage", "exports:artifact-1"),
        mapping={
            "content": "stage",
            "filename": "stage.csv",
            "content_type": "text/csv; charset=utf-8",
        },
    )

    response = await client.get(
        "/api/v1/exports/artifact-1/download",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "report.csv" in response.headers["content-disposition"]
    assert response.text == "a,b\n1,2\n"


async def test_export_download_rejects_missing_artifact(
    client, auth_headers, export_projects_config
):
    response = await client.get(
        "/api/v1/exports/missing/download",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "导出文件不存在", "data": None}


async def test_export_download_sanitizes_content_disposition_filename(
    app_instance, client, auth_headers, export_projects_config
):
    await app_instance.state.redis.hset(
        RedisKey.of("prod", "exports:artifact-danger"),
        mapping={
            "content": "safe",
            "filename": '报告\r\nX-Bad: 1".csv',
            "content_type": "text/csv; charset=utf-8",
        },
    )

    response = await client.get(
        "/api/v1/exports/artifact-danger/download",
        headers=auth_headers,
    )

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "X-Bad" not in disposition
    assert '报告\r\nX-Bad: 1".csv' not in disposition


async def test_export_store_save_sets_ttl(app_instance):
    store = ExportStore(app_instance.state.redis, "prod")

    artifact = await store.save(
        content="hello",
        filename="report.csv",
        content_type="text/csv; charset=utf-8",
    )

    ttl = await app_instance.state.redis.ttl(
        RedisKey.of("prod", f"exports:{artifact.export_id}")
    )
    assert 0 < ttl <= EXPORT_TTL_SECONDS


async def test_export_store_get_decodes_redis_bytes(app_instance):
    key = RedisKey.of("prod", "exports:bytes-artifact")
    await app_instance.state.redis.hset(
        key,
        mapping={
            b"content": b"a,b\n1,2\n",
            b"filename": b"report.csv",
            b"content_type": b"text/csv; charset=utf-8",
        },
    )

    artifact = await ExportStore(app_instance.state.redis, "prod").get("bytes-artifact")

    assert artifact is not None
    assert artifact.content == "a,b\n1,2\n"
    assert artifact.filename == "report.csv"
    assert artifact.content_type == "text/csv; charset=utf-8"


async def test_export_store_get_supports_bytes_hash_keys():
    artifact = await ExportStore(
        FakeRedis(
            {
                b"content": b"a,b\n1,2\n",
                b"filename": b"report.csv",
                b"content_type": b"text/csv; charset=utf-8",
            }
        ),
        "prod",
    ).get("bytes-artifact")

    assert artifact is not None
    assert artifact.content == "a,b\n1,2\n"
    assert artifact.filename == "report.csv"
    assert artifact.content_type == "text/csv; charset=utf-8"


async def test_export_download_requires_authentication(
    client, export_projects_config
):
    response = await client.get(
        "/api/v1/exports/missing/download",
        headers={"X-Project-Id": "prod"},
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40001, "message": "未登录", "data": None}


async def test_export_download_requires_project_id(
    client, make_jwt, export_projects_config
):
    response = await client.get(
        "/api/v1/exports/missing/download",
        headers={"Authorization": f"Bearer {make_jwt(role='admin')}"},
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40004, "message": "项目不存在", "data": None}


async def test_logs_export_csv_generates_downloadable_artifact(
    app_instance, client, auth_headers, export_projects_config
):
    await app_instance.state.redis.set(
        RedisKey.of("prod", RedisKey.RECENT_ERRORS),
        json.dumps(
            [
                {
                    "timestamp": "2026-06-17T09:00:00Z",
                    "level": "ERROR",
                    "service": "message-service",
                    "traceId": "trace-1",
                    "message": "boom",
                }
            ]
        ),
    )
    await app_instance.state.redis.set(
        RedisKey.of("stage", RedisKey.RECENT_ERRORS),
        json.dumps(
            [
                {
                    "timestamp": "2026-06-17T09:00:00Z",
                    "level": "ERROR",
                    "service": "stage-service",
                    "traceId": "trace-2",
                    "message": "ignore",
                }
            ]
        ),
    )

    response = await client.post(
        "/api/v1/logs/export",
        headers=auth_headers,
        json={"format": "csv", "params": {"service": "message-service"}},
    )

    assert response.status_code == 200
    download_url = response.json()["data"]["downloadUrl"]
    downloaded = await client.get(download_url, headers=auth_headers)
    assert downloaded.status_code == 200
    assert "timestamp,level,service,traceId,message" in downloaded.text
    assert "message-service" in downloaded.text
    assert "stage-service" not in downloaded.text


async def test_logs_export_csv_escapes_formula_injection_cells(
    app_instance, client, auth_headers, export_projects_config
):
    await app_instance.state.redis.set(
        RedisKey.of("prod", RedisKey.RECENT_ERRORS),
        json.dumps(
            [
                {
                    "timestamp": "2026-06-17T09:00:00Z",
                    "level": "ERROR",
                    "service": "=cmd|' /C calc'!A0",
                    "traceId": "+trace-1",
                    "message": "@dangerous",
                },
                {
                    "timestamp": "2026-06-17T09:01:00Z",
                    "level": "INFO",
                    "service": "message-service",
                    "traceId": "trace-2",
                    "message": "normal text",
                },
            ]
        ),
    )

    response = await client.post(
        "/api/v1/logs/export",
        headers=auth_headers,
        json={"format": "csv", "params": {}},
    )

    assert response.status_code == 200
    downloaded = await client.get(response.json()["data"]["downloadUrl"], headers=auth_headers)
    rows = list(csv.DictReader(StringIO(downloaded.text)))

    assert rows[0]["service"] == "'=cmd|' /C calc'!A0"
    assert rows[0]["traceId"] == "'+trace-1"
    assert rows[0]["message"] == "'@dangerous"
    assert rows[1]["service"] == "message-service"
    assert rows[1]["traceId"] == "trace-2"
    assert rows[1]["message"] == "normal text"


async def test_logs_export_invalid_format_returns_validation_error(
    client, auth_headers, export_projects_config
):
    response = await client.post(
        "/api/v1/logs/export",
        headers=auth_headers,
        json={"format": "markdown", "params": {}},
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40022, "message": "参数校验失败", "data": None}


async def test_audit_export_json_generates_current_project_artifact(
    app_instance, client, auth_headers, export_projects_config
):
    async with app_instance.state.sessionmaker() as session:
        session.add_all(
            [
                AuditLog(
                    project_id="prod",
                    operator_type="admin",
                    operator_name="admin",
                    action="CONFIG_UPDATE",
                    resource_type="config",
                    result="success",
                ),
                AuditLog(
                    project_id="stage",
                    operator_type="admin",
                    operator_name="admin",
                    action="CONFIG_UPDATE",
                    resource_type="config",
                    result="success",
                ),
            ]
        )
        await session.commit()

    response = await client.post(
        "/api/v1/audit/export",
        headers=auth_headers,
        json={"format": "json", "params": {"operatorType": "admin"}},
    )

    assert response.status_code == 200
    downloaded = await client.get(response.json()["data"]["downloadUrl"], headers=auth_headers)
    payload = json.loads(downloaded.text)
    assert [item["action"] for item in payload] == ["CONFIG_UPDATE"]
    assert all(item["operatorType"] == "admin" for item in payload)


async def test_audit_export_invalid_format_returns_validation_error(
    client, auth_headers, export_projects_config
):
    response = await client.post(
        "/api/v1/audit/export",
        headers=auth_headers,
        json={"format": "markdown", "params": {}},
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40022, "message": "参数校验失败", "data": None}


async def test_audit_export_invalid_datetime_type_returns_validation_error(
    client, auth_headers, export_projects_config
):
    list_response = await client.post(
        "/api/v1/audit/export",
        headers=auth_headers,
        json={"format": "json", "params": {"startTime": ["2026-06-17T00:00:00Z"]}},
    )
    object_response = await client.post(
        "/api/v1/audit/export",
        headers=auth_headers,
        json={"format": "json", "params": {"startTime": {"from": "2026-06-17T00:00:00Z"}}},
    )

    assert list_response.status_code == 200
    assert list_response.json() == {"code": 40022, "message": "参数校验失败", "data": None}
    assert object_response.status_code == 200
    assert object_response.json() == {"code": 40022, "message": "参数校验失败", "data": None}


async def test_rca_export_markdown_includes_full_evidence(
    app_instance, client, auth_headers, export_projects_config
):
    async with app_instance.state.sessionmaker() as session:
        run = AgentRun(
            project_id="prod",
            status="success",
            trigger_source="manual",
            evidence_chain={"metrics": ["heap high"], "logs": ["oom"]},
            timeline=[{"time": "2026-06-17T09:00:00Z", "event": "oom"}],
            root_cause_summary="memory pressure",
        )
        foreign_run = AgentRun(
            project_id="stage",
            status="success",
            trigger_source="manual",
            evidence_chain={"metrics": ["stage metric"]},
            timeline=[{"time": "2026-06-17T09:00:00Z", "event": "stage"}],
            root_cause_summary="stage summary",
        )
        session.add_all([run, foreign_run])
        await session.commit()
        run_id = run.id
        foreign_run_id = foreign_run.id

    response = await client.post(
        f"/api/v1/rca/{run_id}/export",
        headers=auth_headers,
        json={"format": "markdown"},
    )

    assert response.status_code == 200
    downloaded = await client.get(response.json()["data"]["downloadUrl"], headers=auth_headers)
    assert "# RCA Report" in downloaded.text
    assert "memory pressure" in downloaded.text
    assert "heap high" in downloaded.text
    assert "oom" in downloaded.text
    assert "stage summary" not in downloaded.text

    missing = await client.post(
        f"/api/v1/rca/{foreign_run_id}/export",
        headers=auth_headers,
        json={"format": "markdown"},
    )
    assert missing.status_code == 200
    assert missing.json() == {"code": 40004, "message": "RCA 不存在", "data": None}


async def test_rca_export_pdf_returns_pdf_content_type(
    app_instance, client, auth_headers, export_projects_config
):
    async with app_instance.state.sessionmaker() as session:
        run = AgentRun(
            project_id="prod",
            status="success",
            trigger_source="manual",
            evidence_chain={"metrics": ["heap high"], "logs": ["oom"]},
            timeline=[{"time": "2026-06-17T09:00:00Z", "event": "oom"}],
            root_cause_summary="memory pressure",
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    response = await client.post(
        f"/api/v1/rca/{run_id}/export",
        headers=auth_headers,
        json={"format": "pdf"},
    )

    assert response.status_code == 200
    downloaded = await client.get(response.json()["data"]["downloadUrl"], headers=auth_headers)
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/pdf")
    assert "memory pressure" in downloaded.text


async def test_rca_export_invalid_format_returns_validation_error(
    app_instance, client, auth_headers, export_projects_config
):
    async with app_instance.state.sessionmaker() as session:
        run = AgentRun(
            project_id="prod",
            status="success",
            trigger_source="manual",
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    response = await client.post(
        f"/api/v1/rca/{run_id}/export",
        headers=auth_headers,
        json={"format": "json"},
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40022, "message": "参数校验失败", "data": None}
