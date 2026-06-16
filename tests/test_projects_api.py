from httpx import ASGITransport, AsyncClient

from app.core.projects import reset_projects_config


async def test_projects_api_requires_jwt(client):
    response = await client.get("/api/v1/projects")

    assert response.status_code == 200
    assert response.json()["code"] == 40001


async def test_projects_api_returns_enabled_projects_without_project_header(
    make_jwt, monkeypatch, tmp_path
):
    from app.main import get_app

    path = tmp_path / "projects.yaml"
    path.write_text(
        """
default_project: demo
projects:
  demo:
    name: Demo
    metric_profile: java
  disabled:
    name: Disabled
    metric_profile: java
    enabled: false
  analytics:
    name: Analytics
    metric_profile: java
    """.strip()
    )
    monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(path))
    from app.core.config import get_settings

    get_settings.cache_clear()
    reset_projects_config()
    app = get_app()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v1/projects",
                headers={"Authorization": f"Bearer {make_jwt(role='admin')}"},
            )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": [
            {"id": "demo", "name": "Demo", "metricProfile": "java"},
            {"id": "analytics", "name": "Analytics", "metricProfile": "java"},
        ],
    }


async def test_projects_api_forbids_non_admin(client, make_jwt):
    response = await client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {make_jwt(role='user')}"},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 40003
