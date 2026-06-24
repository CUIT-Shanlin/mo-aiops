from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text

from app.core.constants import RedisKey
from app.core.projects import ProjectConfig, ProjectsConfig
from app.models.audit_log import AuditLog
from app.models.heal_action import HealAction
import app.api.settings as settings_api


class FakeK8sProvider:
    def __init__(self, project_id: str, logs_by_project: dict[str, str]) -> None:
        self.project_id = project_id
        self.logs_by_project = logs_by_project
        self.query_calls: list[dict[str, object]] = []
        self.notify_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []
        self.notify_error: Exception | None = None
        self.stream_error_after_lines: int | None = None
        self.support_stream: bool = True

    async def query(self, command_type: str, **kw):
        self.query_calls.append({"command_type": command_type, **kw})
        if command_type == "pod_logs":
            return self.logs_by_project[self.project_id]
        if command_type == "list_pods":
            return {
                "items": [
                    {
                        "metadata": {
                            "name": "message-0",
                            "namespace": "prod",
                            "labels": {"app": "message-service"},
                        },
                        "status": {
                            "phase": "Running",
                            "pod_ip": "10.0.0.1",
                            "container_statuses": [
                                {"restart_count": 0, "ready": True},
                            ],
                        },
                    },
                    {
                        "metadata": {
                            "name": "crash-0",
                            "namespace": "prod",
                            "labels": {},
                        },
                        "status": {
                            "phase": "Running",
                            "container_statuses": [
                                {
                                    "restart_count": 3,
                                    "ready": False,
                                    "state": {
                                        "waiting": {"reason": "CrashLoopBackOff"},
                                    },
                                }
                            ],
                        },
                    },
                ]
            }
        if command_type == "list_nodes":
            return {
                "items": [
                    {
                        "metadata": {"name": "node-a"},
                        "status": {
                            "conditions": [
                                {"type": "Ready", "status": "True"},
                            ],
                        },
                    }
                ]
            }
        if command_type == "list_namespaces":
            return {"items": [{"metadata": {"name": "prod"}}]}
        if command_type == "list_deployments":
            return {"items": [{"metadata": {"name": "message-service"}}]}
        raise AssertionError(f"unexpected command_type: {command_type}")

    async def notify(self, action: str, **kw):
        self.notify_calls.append({"action": action, **kw})
        if self.notify_error is not None:
            raise self.notify_error
        return {"accepted": True}

    async def stream_pod_logs(self, **kw) -> AsyncIterator[str]:
        if not self.support_stream:
            raise AttributeError("streaming disabled")
        self.stream_calls.append(kw)
        for idx, line in enumerate(self.logs_by_project[self.project_id].splitlines(), start=1):
            yield line
            if self.stream_error_after_lines == idx:
                raise RuntimeError("stream exploded")


class NoStreamK8sProvider:
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id


@pytest.fixture(autouse=True)
async def _clean_k8s_api_tables(app_instance):
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
def k8s_projects_config(app_instance):
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


@pytest.fixture
def fake_k8s_factory(app_instance):
    logs_by_project = {
        "prod": "prod line 1\nprod line 2",
        "stage": "stage line 1\nstage line 2",
    }
    providers: dict[str, FakeK8sProvider] = {}

    def factory(project_id: str) -> FakeK8sProvider:
        provider = providers.get(project_id)
        if provider is None:
            provider = FakeK8sProvider(project_id, logs_by_project)
            providers[project_id] = provider
        return provider

    app_instance.state.k8s_provider_factory = factory
    return providers


async def test_k8s_api_requires_admin_jwt(client, k8s_projects_config):
    logs_response = await client.get(
        "/api/v1/k8s/pods/message-0/logs",
        params={"namespace": "prod", "tail": 100},
        headers={"X-Project-Id": "prod"},
    )
    restart_response = await client.post(
        "/api/v1/k8s/pods/message-0/restart",
        params={"namespace": "prod"},
        headers={"X-Project-Id": "prod"},
    )

    assert logs_response.status_code == 200
    assert logs_response.json()["code"] == 40001
    assert restart_response.status_code == 200
    assert restart_response.json()["code"] == 40001


async def test_k8s_api_requires_known_enabled_project(
    client,
    make_jwt,
    k8s_projects_config,
):
    headers = {"Authorization": f"Bearer {make_jwt(role='admin')}"}

    missing = await client.get(
        "/api/v1/k8s/pods/message-0/logs",
        params={"namespace": "prod"},
        headers=headers,
    )
    disabled = await client.get(
        "/api/v1/k8s/pods/message-0/logs",
        params={"namespace": "prod"},
        headers={**headers, "X-Project-Id": "disabled"},
    )

    assert missing.status_code == 200
    assert missing.json() == {"code": 40004, "message": "项目不存在", "data": None}
    assert disabled.status_code == 200
    assert disabled.json() == missing.json()


async def test_k8s_overview_and_list_endpoints_use_project_provider(
    client,
    auth_headers,
    k8s_projects_config,
    fake_k8s_factory,
):
    overview = await client.get("/api/v1/k8s/overview", headers=auth_headers)
    pods = await client.get(
        "/api/v1/k8s/pods",
        params={"namespace": "prod", "page": 1, "pageSize": 10},
        headers=auth_headers,
    )
    nodes = await client.get("/api/v1/k8s/nodes", headers=auth_headers)
    namespaces = await client.get("/api/v1/k8s/namespaces", headers=auth_headers)
    deployments = await client.get(
        "/api/v1/k8s/deployments",
        params={"namespace": "prod"},
        headers=auth_headers,
    )

    assert overview.status_code == 200
    assert overview.json()["data"] == {
        "nodeCount": 1,
        "readyNodeCount": 1,
        "podCount": 2,
        "runningPodCount": 2,
        "crashLoopCount": 1,
        "clusterCpuUsage": 0,
        "totalCores": 0,
        "usedCores": 0,
    }
    assert pods.json()["data"]["total"] == 2
    assert pods.json()["data"]["items"][0]["name"] == "message-0"
    assert nodes.json()["data"][0]["name"] == "node-a"
    assert namespaces.json()["data"] == ["prod"]
    assert deployments.json()["data"] == ["message-service"]
    assert [call["command_type"] for call in fake_k8s_factory["prod"].query_calls[:5]] == [
        "list_pods",
        "list_nodes",
        "list_pods",
        "list_nodes",
        "list_namespaces",
    ]
    assert fake_k8s_factory["prod"].query_calls[-1] == {
        "command_type": "list_deployments",
        "namespace": "prod",
    }


async def test_get_pod_logs_returns_plain_text_and_calls_project_provider(
    client,
    auth_headers,
    k8s_projects_config,
    fake_k8s_factory,
):
    response = await client.get(
        "/api/v1/k8s/pods/message-0/logs",
        params={"namespace": "prod", "tail": 100},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "prod line 1\nprod line 2"
    assert fake_k8s_factory["prod"].query_calls == [
        {
            "command_type": "pod_logs",
            "pod_name": "message-0",
            "namespace": "prod",
            "tail": 100,
        }
    ]


async def test_get_pod_logs_stream_returns_sse_lines(
    client,
    auth_headers,
    k8s_projects_config,
    fake_k8s_factory,
):
    async with client.stream(
        "GET",
        "/api/v1/k8s/pods/message-0/logs/stream",
        params={"namespace": "prod", "tail": 100},
        headers=auth_headers,
    ) as response:
        body = await response.aread()

    text_body = body.decode("utf-8")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: prod line 1\n\n" in text_body
    assert "data: prod line 2\n\n" in text_body
    assert fake_k8s_factory["prod"].stream_calls == [
        {
            "pod_name": "message-0",
            "namespace": "prod",
            "tail": 100,
        }
    ]


async def test_pod_logs_stream_accepts_project_id_query_for_eventsource(
    client,
    make_jwt,
    k8s_projects_config,
    fake_k8s_factory,
):
    headers = {"Authorization": f"Bearer {make_jwt(role='admin')}"}

    response = await client.get(
        "/api/v1/k8s/pods/message-0/logs/stream",
        params={"namespace": "prod", "tail": 2, "project_id": "prod"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: prod line 1" in response.text
    assert fake_k8s_factory["prod"].stream_calls == [
        {"pod_name": "message-0", "namespace": "prod", "tail": 2}
    ]


async def test_pod_logs_stream_rejects_conflicting_header_and_query_project_id(
    client,
    make_jwt,
    k8s_projects_config,
    fake_k8s_factory,
):
    headers = {
        "Authorization": f"Bearer {make_jwt(role='admin')}",
        "X-Project-Id": "prod",
    }

    response = await client.get(
        "/api/v1/k8s/pods/message-0/logs/stream",
        params={"namespace": "prod", "tail": 2, "project_id": "stage"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 40022,
        "message": "X-Project-Id 与 project_id 不一致",
        "data": None,
    }
    assert "prod" not in fake_k8s_factory
    assert "stage" not in fake_k8s_factory


async def test_restart_pod_creates_agent_heal_action_and_audit(
    app_instance,
    client,
    auth_headers,
    k8s_projects_config,
    fake_k8s_factory,
):
    response = await client.post(
        "/api/v1/k8s/pods/message-0/restart",
        params={"namespace": "prod"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0

    async with app_instance.state.sessionmaker() as session:
        actions = (
            await session.execute(select(HealAction).where(HealAction.project_id == "prod"))
        ).scalars().all()
        audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()

    assert len(actions) == 1
    assert actions[0].action_type == "pod_restart"
    assert actions[0].target_resource == "pod/message-0"
    assert actions[0].target_namespace == "prod"
    assert actions[0].operator == "aiops_agent"
    assert actions[0].status == "success"
    assert len(audits) == 2
    assert [audit.action for audit in audits] == ["POD_RESTART", "POD_RESTART"]
    assert [audit.result for audit in audits] == ["requested", "success"]
    assert all(audit.operator_type == "aiops_agent" for audit in audits)
    assert all(audit.resource_type == "heal_action" for audit in audits)
    assert fake_k8s_factory["prod"].notify_calls == [
        {
            "action": "pod_restart",
            "pod_name": "message-0",
            "namespace": "prod",
        }
    ]


async def test_restart_pod_accepts_documented_body_and_returns_task_id(
    client,
    auth_headers,
    k8s_projects_config,
    fake_k8s_factory,
):
    response = await client.post(
        "/api/v1/k8s/pods/message-0/restart",
        headers=auth_headers,
        json={"namespace": "prod", "reason": "CrashLoopBackOff 自动修复"},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"]["success"] is True
    assert response.json()["data"]["taskId"]
    assert fake_k8s_factory["prod"].notify_calls == [
        {
            "action": "pod_restart",
            "pod_name": "message-0",
            "namespace": "prod",
        }
    ]


async def test_restart_pod_manual_creates_admin_heal_action_and_audit(
    app_instance,
    client,
    auth_headers,
    k8s_projects_config,
    fake_k8s_factory,
):
    response = await client.post(
        "/api/v1/k8s/pods/message-0/restart/manual",
        params={"namespace": "prod"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0

    async with app_instance.state.sessionmaker() as session:
        actions = (
            await session.execute(select(HealAction).where(HealAction.project_id == "prod"))
        ).scalars().all()
        audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()

    assert len(actions) == 1
    assert actions[0].action_type == "pod_restart"
    assert actions[0].operator == "admin"
    assert actions[0].status == "success"
    assert len(audits) == 2
    assert [audit.action for audit in audits] == ["POD_RESTART", "POD_RESTART"]
    assert [audit.result for audit in audits] == ["requested", "success"]
    assert all(audit.operator_type == "admin" for audit in audits)
    assert audits[-1].operator_uid == "u1"
    assert fake_k8s_factory["prod"].notify_calls == [
        {
            "action": "pod_restart",
            "pod_name": "message-0",
            "namespace": "prod",
        }
    ]


async def test_k8s_endpoints_enforce_project_isolation(
    app_instance,
    client,
    make_jwt,
    k8s_projects_config,
    fake_k8s_factory,
):
    headers = {
        "Authorization": f"Bearer {make_jwt(role='admin', sub='stage-admin')}",
        "X-Project-Id": "stage",
    }

    logs_response = await client.get(
        "/api/v1/k8s/pods/message-0/logs",
        params={"namespace": "prod", "tail": 20},
        headers=headers,
    )
    restart_response = await client.post(
        "/api/v1/k8s/pods/message-0/restart/manual",
        params={"namespace": "prod"},
        headers=headers,
    )

    assert logs_response.status_code == 200
    assert logs_response.text == "stage line 1\nstage line 2"
    assert restart_response.status_code == 200
    assert fake_k8s_factory["stage"].query_calls == [
        {
            "command_type": "pod_logs",
            "pod_name": "message-0",
            "namespace": "prod",
            "tail": 20,
        }
    ]
    assert fake_k8s_factory["stage"].notify_calls == [
        {
            "action": "pod_restart",
            "pod_name": "message-0",
            "namespace": "prod",
        }
    ]

    async with app_instance.state.sessionmaker() as session:
        prod_actions = (
            await session.execute(select(HealAction).where(HealAction.project_id == "prod"))
        ).scalars().all()
        stage_actions = (
            await session.execute(select(HealAction).where(HealAction.project_id == "stage"))
        ).scalars().all()
        prod_audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()
        stage_audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "stage")
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()

    assert prod_actions == []
    assert len(stage_actions) == 1
    assert stage_actions[0].status == "success"
    assert prod_audits == []
    assert len(stage_audits) == 2
    assert [audit.result for audit in stage_audits] == ["requested", "success"]


async def test_restart_pod_notify_failure_persists_failed_action_and_audits(
    app_instance,
    client,
    auth_headers,
    k8s_projects_config,
    fake_k8s_factory,
):
    fake_k8s_factory["prod"] = FakeK8sProvider(
        "prod",
        {"prod": "prod line 1\nprod line 2", "stage": "stage line 1\nstage line 2"},
    )
    fake_k8s_factory["prod"].notify_error = RuntimeError("boom")

    response = await client.post(
        "/api/v1/k8s/pods/message-0/restart",
        params={"namespace": "prod"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 50001,
        "message": "Kubernetes API 调用失败",
        "data": None,
    }

    async with app_instance.state.sessionmaker() as session:
        actions = (
            await session.execute(select(HealAction).where(HealAction.project_id == "prod"))
        ).scalars().all()
        audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()

    assert len(actions) == 1
    assert actions[0].operator == "aiops_agent"
    assert actions[0].status == "failed"
    assert actions[0].result_message == "boom"
    assert actions[0].finished_at is not None
    assert len(audits) == 2
    assert [audit.action for audit in audits] == ["POD_RESTART", "POD_RESTART"]
    assert [audit.result for audit in audits] == ["requested", "failed"]
    assert all(audit.operator_type == "aiops_agent" for audit in audits)


async def test_update_config_accepts_reason_and_records_it(
    client,
    make_jwt,
    k8s_projects_config,
    app_instance,
):
    headers = {
        "Authorization": f"Bearer {make_jwt(role='admin', sub='admin')}",
        "X-Project-Id": "prod",
    }

    response = await client.put(
        "/api/v1/settings/configs/aiops.detect_interval_sec",
        headers=headers,
        json={"value": "45", "reason": "frontend smoke test"},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 0
    async with app_instance.state.sessionmaker() as session:
        rows = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "prod")
                .where(AuditLog.action == "CONFIG_UPDATE")
                .order_by(AuditLog.id)
            )
        ).scalars().all()
    assert [row.result for row in rows] == ["requested", "success"]
    assert [row.reason for row in rows] == [
        "frontend smoke test",
        "frontend smoke test",
    ]


async def test_get_pod_logs_stream_yields_error_event_after_first_chunk(
    client,
    auth_headers,
    k8s_projects_config,
    fake_k8s_factory,
):
    fake_k8s_factory.setdefault(
        "prod",
        FakeK8sProvider(
            "prod",
            {"prod": "prod line 1\nprod line 2", "stage": "stage line 1\nstage line 2"},
        ),
    )
    fake_k8s_factory["prod"].stream_error_after_lines = 1

    async with client.stream(
        "GET",
        "/api/v1/k8s/pods/message-0/logs/stream",
        params={"namespace": "prod", "tail": 100},
        headers=auth_headers,
    ) as response:
        body = await response.aread()

    text_body = body.decode("utf-8")
    assert response.status_code == 200
    assert "data: prod line 1\n\n" in text_body
    assert "event: error\ndata: Kubernetes API 调用失败\n\n" in text_body


async def test_get_pod_logs_stream_requires_supported_provider_before_streaming(
    client,
    auth_headers,
    k8s_projects_config,
    fake_k8s_factory,
):
    fake_k8s_factory["prod"] = NoStreamK8sProvider("prod")

    response = await client.get(
        "/api/v1/k8s/pods/message-0/logs/stream",
        params={"namespace": "prod", "tail": 100},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 50001,
        "message": "Kubernetes Provider 未配置",
        "data": None,
    }


async def test_settings_categories_returns_api_doc_categories(
    client,
    auth_headers,
    k8s_projects_config,
):
    response = await client.get(
        "/api/v1/settings/categories",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": ["连接层配置", "限流配置", "缓存配置", "AIOps 配置"],
    }


async def test_settings_configs_merges_redis_overrides_for_current_project(
    app_instance,
    client,
    auth_headers,
    k8s_projects_config,
):
    await app_instance.state.redis.hset(
        RedisKey.of("prod", RedisKey.CONFIG),
        mapping={
            "aiops.detect_interval_sec": "90",
            "aiops.dedup_window_sec": "600",
        },
    )
    await app_instance.state.redis.hset(
        RedisKey.of("stage", RedisKey.CONFIG),
        mapping={"aiops.detect_interval_sec": "15"},
    )

    response = await client.get(
        "/api/v1/settings/configs",
        params={"category": "AIOps 配置"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": [
            {
                "category": "AIOps 配置",
                "key": "aiops.detect_interval_sec",
                "name": "异常检测周期",
                "value": "90",
                "unit": "秒",
                "riskLevel": "medium",
            },
            {
                "category": "AIOps 配置",
                "key": "aiops.auto_heal_enabled",
                "name": "自动自愈开关",
                "value": "开启",
                "unit": "",
                "riskLevel": "high",
            },
            {
                "category": "AIOps 配置",
                "key": "aiops.dedup_window_sec",
                "name": "告警去重窗口",
                "value": "600",
                "unit": "秒",
                "riskLevel": "medium",
            },
        ],
    }


async def test_settings_configs_returns_empty_for_non_aiops_category(
    client,
    auth_headers,
    k8s_projects_config,
):
    response = await client.get(
        "/api/v1/settings/configs",
        params={"category": "连接层配置"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": [],
    }


async def test_update_setting_writes_project_redis_override_and_requested_success_audits(
    app_instance,
    client,
    auth_headers,
    k8s_projects_config,
):
    response = await client.put(
        "/api/v1/settings/configs/aiops.detect_interval_sec",
        json={"value": "120"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {"success": True},
    }
    assert (
        await app_instance.state.redis.hget(
            RedisKey.of("prod", RedisKey.CONFIG),
            "aiops.detect_interval_sec",
        )
    ) == "120"
    assert (
        await app_instance.state.redis.hget(
            RedisKey.of("stage", RedisKey.CONFIG),
            "aiops.detect_interval_sec",
        )
    ) is None

    async with app_instance.state.sessionmaker() as session:
        audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()

    assert len(audits) == 2
    assert [audit.action for audit in audits] == ["CONFIG_UPDATE", "CONFIG_UPDATE"]
    assert [audit.result for audit in audits] == ["requested", "success"]
    assert all(audit.operator_type == "admin" for audit in audits)
    assert all(audit.operator_uid == "u1" for audit in audits)
    assert all(audit.resource_type == "config" for audit in audits)
    assert all(audit.resource_id == "aiops.detect_interval_sec" for audit in audits)
    assert all(audit.before_state == {"value": "60"} for audit in audits)
    assert all(audit.after_state == {"value": "120"} for audit in audits)


async def test_high_risk_setting_update_with_confirm_succeeds_and_writes_audits(
    app_instance,
    client,
    auth_headers,
    k8s_projects_config,
):
    response = await client.put(
        "/api/v1/settings/configs/aiops.auto_heal_enabled",
        json={"value": "关闭", "confirm": True},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {"success": True},
    }
    assert (
        await app_instance.state.redis.hget(
            RedisKey.of("prod", RedisKey.CONFIG),
            "aiops.auto_heal_enabled",
        )
    ) == "关闭"

    async with app_instance.state.sessionmaker() as session:
        audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()

    assert len(audits) == 2
    assert [audit.result for audit in audits] == ["requested", "success"]
    assert all(audit.resource_id == "aiops.auto_heal_enabled" for audit in audits)
    assert all(audit.before_state == {"value": "开启"} for audit in audits)
    assert all(audit.after_state == {"value": "关闭"} for audit in audits)


async def test_update_setting_unknown_key_returns_not_found_without_redis_or_audit(
    app_instance,
    client,
    auth_headers,
    k8s_projects_config,
):
    response = await client.put(
        "/api/v1/settings/configs/aiops.unknown",
        json={"value": "x"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 40004,
        "message": "配置项不存在",
        "data": None,
    }
    assert await app_instance.state.redis.hgetall(RedisKey.of("prod", RedisKey.CONFIG)) == {}

    async with app_instance.state.sessionmaker() as session:
        audits = (
            await session.execute(select(AuditLog).where(AuditLog.project_id == "prod"))
        ).scalars().all()

    assert audits == []


async def test_update_setting_redis_failure_records_requested_and_failed_audits(
    app_instance,
    client,
    auth_headers,
    k8s_projects_config,
    monkeypatch,
):
    original_hset = app_instance.state.redis.hset

    async def broken_hset(name, key=None, value=None, mapping=None, items=None):
        if name == RedisKey.of("prod", RedisKey.CONFIG):
            raise RuntimeError("redis write exploded")
        return await original_hset(name, key=key, value=value, mapping=mapping, items=items)

    monkeypatch.setattr(app_instance.state.redis, "hset", broken_hset)

    response = await client.put(
        "/api/v1/settings/configs/aiops.detect_interval_sec",
        json={"value": "999"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 50002,
        "message": "配置更新失败",
        "data": None,
    }
    assert (
        await app_instance.state.redis.hget(
            RedisKey.of("prod", RedisKey.CONFIG),
            "aiops.detect_interval_sec",
        )
    ) is None

    async with app_instance.state.sessionmaker() as session:
        audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()

    assert len(audits) == 2
    assert [audit.result for audit in audits] == ["requested", "failed"]
    assert audits[0].before_state == {"value": "60"}
    assert audits[0].after_state == {"value": "999"}
    assert audits[1].before_state == {"value": "60"}
    assert audits[1].after_state == {"value": "999"}
    assert audits[1].reason == "redis write exploded"


async def test_update_setting_success_audit_failure_still_returns_success(
    app_instance,
    client,
    auth_headers,
    k8s_projects_config,
    monkeypatch,
):
    original_record = settings_api._record_config_audit

    async def flaky_record(*, result, **kwargs):
        if result == "success":
            raise RuntimeError("success audit exploded")
        await original_record(result=result, **kwargs)

    monkeypatch.setattr(settings_api, "_record_config_audit", flaky_record)

    response = await client.put(
        "/api/v1/settings/configs/aiops.detect_interval_sec",
        json={"value": "180"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {"success": True},
    }
    assert (
        await app_instance.state.redis.hget(
            RedisKey.of("prod", RedisKey.CONFIG),
            "aiops.detect_interval_sec",
        )
    ) == "180"

    async with app_instance.state.sessionmaker() as session:
        audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()

    assert len(audits) == 1
    assert audits[0].result == "requested"
    assert audits[0].before_state == {"value": "60"}
    assert audits[0].after_state == {"value": "180"}


async def test_settings_history_returns_project_scoped_config_audits(
    app_instance,
    client,
    auth_headers,
    k8s_projects_config,
):
    async with app_instance.state.sessionmaker() as session:
        prod_audit = AuditLog(
            project_id="prod",
            operator_uid="u1",
            operator_type="admin",
            operator_name="admin",
            action="CONFIG_UPDATE",
            resource_type="config",
            resource_id="aiops.detect_interval_sec",
            before_state={"value": "60"},
            after_state={"value": "120"},
            result="success",
        )
        stage_audit = AuditLog(
            project_id="stage",
            operator_uid="u2",
            operator_type="admin",
            operator_name="stage-admin",
            action="CONFIG_UPDATE",
            resource_type="config",
            resource_id="aiops.auto_heal_enabled",
            before_state={"value": "开启"},
            after_state={"value": "关闭"},
            result="success",
        )
        other_action = AuditLog(
            project_id="prod",
            operator_uid="u1",
            operator_type="admin",
            operator_name="admin",
            action="POD_RESTART",
            resource_type="heal_action",
            resource_id="1",
            result="success",
        )
        session.add_all([prod_audit, stage_audit, other_action])
        await session.commit()

    response = await client.get(
        "/api/v1/settings/history",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["message"] == "success"
    assert body["data"]["total"] == 1
    assert body["data"]["page"] == 1
    assert body["data"]["pageSize"] == 20
    assert len(body["data"]["items"]) == 1
    assert body["data"]["items"][0]["action"] == "CONFIG_UPDATE"
    assert body["data"]["items"][0]["targetObject"] == "config"
    assert body["data"]["items"][0]["targetResource"] == "aiops.detect_interval_sec"
    assert body["data"]["items"][0]["result"] == "success"
    assert (
        "before={'value': '60'} after={'value': '120'}"
        == body["data"]["items"][0]["details"]
    )


async def test_high_risk_setting_update_requires_confirm(
    client,
    auth_headers,
    k8s_projects_config,
):
    response = await client.put(
        "/api/v1/settings/configs/aiops.auto_heal_enabled",
        json={"value": "关闭"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 40022,
        "message": "参数校验失败",
        "data": None,
    }
