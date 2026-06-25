from __future__ import annotations

from sqlalchemy import select, text

from app.core.projects import ProjectConfig, ProjectsConfig
from app.models.audit_log import AuditLog


def _set_projects(app_instance) -> None:
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
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


async def test_create_user_persists_and_writes_audit(client, make_jwt, app_instance):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin-1"))

    response = await client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "id": "user-1001",
            "username": "zhangsan",
            "email": "zhangsan@mochat.com",
            "avatar": "https://cdn/avatar.png",
            "riskLevel": "normal",
            "onlineStatus": "offline",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["id"] == "user-1001"
    assert data["username"] == "zhangsan"
    assert data["email"] == "zhangsan@mochat.com"
    assert data["avatar"] == "https://cdn/avatar.png"
    assert data["riskLevel"] == "normal"
    assert data["onlineStatus"] == "offline"
    assert data["isBanned"] is False
    assert data["todayMessages"] == 0
    assert data["lastLogin"] is None
    assert data["registeredAt"] is not None

    listed = await client.get("/api/v1/users", headers=headers)
    ids = [item["id"] for item in listed.json()["data"]["items"]]
    assert "user-1001" in ids

    async with app_instance.state.sessionmaker() as session:
        audits = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.project_id == "prod")
                .order_by(AuditLog.id)
            )
        ).scalars().all()
    actions = [audit.action for audit in audits]
    assert "USER_CREATE" in actions
    create_audit = next(audit for audit in audits if audit.action == "USER_CREATE")
    assert create_audit.resource_id == "user-1001"
    assert create_audit.resource_type == "user"
    assert create_audit.result == "success"


async def test_create_user_duplicate_id_returns_validation_error(
    client, make_jwt, app_instance
):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin-1"))

    first = await client.post(
        "/api/v1/users",
        headers=headers,
        json={"id": "dup-1", "username": "first"},
    )
    duplicate = await client.post(
        "/api/v1/users",
        headers=headers,
        json={"id": "dup-1", "username": "second"},
    )

    assert first.json()["code"] == 0
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "code": 40022,
        "message": "用户 ID 已存在",
        "data": None,
    }


async def test_update_user_changes_fields_and_writes_audit(
    client, make_jwt, app_instance
):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin-1"))
    await client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "id": "user-2002",
            "username": "lisi",
            "email": "lisi@mochat.com",
            "riskLevel": "normal",
        },
    )

    response = await client.put(
        "/api/v1/users/user-2002",
        headers=headers,
        json={
            "username": "lisi_new",
            "email": "ls@mochat.com",
            "riskLevel": "warning",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["username"] == "lisi_new"
    assert data["email"] == "ls@mochat.com"
    assert data["riskLevel"] == "warning"

    detail = await client.get("/api/v1/users/user-2002", headers=headers)
    assert detail.json()["data"]["username"] == "lisi_new"

    async with app_instance.state.sessionmaker() as session:
        audits = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.project_id == "prod", AuditLog.action == "USER_UPDATE"
                )
                .order_by(AuditLog.id)
            )
        ).scalars().all()
    assert len(audits) == 1
    assert audits[0].resource_id == "user-2002"
    assert audits[0].before_state["username"] == "lisi"
    assert audits[0].after_state["username"] == "lisi_new"
    assert audits[0].after_state["riskLevel"] == "warning"


async def test_update_user_omits_unset_fields(client, make_jwt, app_instance):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin-1"))
    await client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "id": "user-3003",
            "username": "wangwu",
            "email": "wangwu@mochat.com",
            "riskLevel": "normal",
        },
    )

    response = await client.put(
        "/api/v1/users/user-3003",
        headers=headers,
        json={"riskLevel": "high"},
    )

    data = response.json()["data"]
    assert data["riskLevel"] == "high"
    assert data["username"] == "wangwu"
    assert data["email"] == "wangwu@mochat.com"


async def test_update_missing_user_returns_not_found(client, make_jwt, app_instance):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin-1"))

    response = await client.put(
        "/api/v1/users/missing",
        headers=headers,
        json={"username": "nobody"},
    )

    assert response.json() == {"code": 40004, "message": "用户不存在", "data": None}


async def test_delete_user_removes_row_and_writes_audit(client, make_jwt, app_instance):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin-1"))
    await client.post(
        "/api/v1/users",
        headers=headers,
        json={"id": "user-4004", "username": "zhaoliu"},
    )
    stats_before = await client.get("/api/v1/users/stats", headers=headers)

    response = await client.delete("/api/v1/users/user-4004", headers=headers)

    assert response.status_code == 200
    assert response.json()["data"] == {"success": True}

    detail = await client.get("/api/v1/users/user-4004", headers=headers)
    assert detail.json()["code"] == 40004

    stats_after = await client.get("/api/v1/users/stats", headers=headers)
    assert stats_after.json()["data"]["total"] == stats_before.json()["data"]["total"] - 1

    async with app_instance.state.sessionmaker() as session:
        audits = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.project_id == "prod", AuditLog.action == "USER_DELETE"
                )
                .order_by(AuditLog.id)
            )
        ).scalars().all()
    assert len(audits) == 1
    assert audits[0].resource_id == "user-4004"
    assert audits[0].result == "success"


async def test_delete_missing_user_returns_not_found(client, make_jwt, app_instance):
    await _clean(app_instance)
    _set_projects(app_instance)
    headers = _headers(make_jwt(role="admin", sub="admin-1"))

    response = await client.delete("/api/v1/users/missing", headers=headers)

    assert response.json() == {"code": 40004, "message": "用户不存在", "data": None}


async def test_user_crud_is_project_scoped(client, make_jwt, app_instance):
    await _clean(app_instance)
    _set_projects(app_instance)
    prod_headers = _headers(make_jwt(role="admin", sub="admin-1"), "prod")
    stage_headers = _headers(make_jwt(role="admin", sub="admin-1"), "stage")

    await client.post(
        "/api/v1/users",
        headers=prod_headers,
        json={"id": "shared-id", "username": "prod-user"},
    )
    stage_create = await client.post(
        "/api/v1/users",
        headers=stage_headers,
        json={"id": "shared-id", "username": "stage-user"},
    )

    assert stage_create.json()["code"] == 0

    prod_list = await client.get("/api/v1/users", headers=prod_headers)
    stage_list = await client.get("/api/v1/users", headers=stage_headers)
    prod_names = [item["username"] for item in prod_list.json()["data"]["items"]]
    stage_names = [item["username"] for item in stage_list.json()["data"]["items"]]
    assert "prod-user" in prod_names
    assert "stage-user" not in prod_names
    assert "stage-user" in stage_names
    assert "prod-user" not in stage_names

    stage_delete = await client.delete("/api/v1/users/shared-id", headers=stage_headers)
    assert stage_delete.json()["code"] == 0
    prod_still = await client.get("/api/v1/users/shared-id", headers=prod_headers)
    assert prod_still.json()["code"] == 0
    assert prod_still.json()["data"]["username"] == "prod-user"
