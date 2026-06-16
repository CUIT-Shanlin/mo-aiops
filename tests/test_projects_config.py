import pytest
from pydantic import ValidationError

from app.core.projects import (
    LokiAuthType,
    ProjectConfig,
    ProjectsConfig,
    iter_enabled_projects,
    load_projects_config,
    reset_projects_config,
)


def test_load_projects_config_expands_env_default(tmp_path, monkeypatch):
    monkeypatch.delenv("PROM_URL", raising=False)
    path = tmp_path / "projects.yaml"
    path.write_text(
        """
default_project: demo
projects:
  demo:
    name: Demo
    datasources:
      prometheus:
        base_url: "${PROM_URL:-http://prom:9090}"
""".strip()
    )

    reset_projects_config()
    config = load_projects_config(path)

    assert config.projects["demo"].datasource_configs["prometheus"].base_url == "http://prom:9090"


def test_default_project_missing_raises():
    with pytest.raises(ValidationError) as exc:
        ProjectsConfig(
            default_project="missing",
            projects={"demo": ProjectConfig(name="Demo")},
        )

    assert "default_project" in str(exc.value)


def test_unknown_metric_profile_raises():
    with pytest.raises(ValidationError) as exc:
        ProjectConfig(name="Demo", metric_profile="does-not-exist")

    assert "unknown metric profile" in str(exc.value)


def test_prometheus_datasource_schema_validation():
    with pytest.raises(ValidationError) as exc:
        ProjectConfig(name="P1", datasources={"prometheus": {}})

    assert "prometheus" in str(exc.value)


def test_loki_auth_type_string_parses():
    project = ProjectConfig(
        name="Demo",
        datasources={
            "loki": {
                "base_url": "http://loki:3100",
                "auth_type": "NoAuth",
            }
        },
    )

    assert project.datasource_configs["loki"].auth_type is LokiAuthType.NO_AUTH


def test_iter_enabled_projects_filters_disabled():
    config = ProjectsConfig(
        default_project="demo",
        projects={
            "demo": ProjectConfig(name="Demo"),
            "enabled": ProjectConfig(name="Enabled"),
            "disabled": ProjectConfig(name="Disabled", enabled=False),
        },
    )

    assert [project_id for project_id, _ in iter_enabled_projects(config)] == ["demo", "enabled"]


def test_unknown_datasource_key_is_preserved():
    project = ProjectConfig(
        name="Demo",
        datasources={
            "custom": {"foo": "bar"},
        },
    )

    assert project.datasources["custom"] == {"foo": "bar"}
    assert "custom" not in project.datasource_configs


def test_sensitive_fields_not_in_repr_or_dump():
    project = ProjectConfig(
        name="Demo",
        datasources={
            "kubernetes": {
                "mode": "token",
                "api_server": "https://k8s:6443",
                "token": "secret-token",
            },
            "tempo": {
                "base_url": "http://tempo:3200",
                "username": "tempo-user",
                "password": "secret-pass",
            },
        },
    )

    text = repr(project)
    dumped = project.model_dump()
    dumped_json = project.model_dump_json()

    assert "secret-token" not in text
    assert "secret-pass" not in text
    assert "secret-token" not in dumped_json
    assert "secret-pass" not in dumped_json
    assert "datasources" not in dumped
    assert "datasource_configs" not in dumped
