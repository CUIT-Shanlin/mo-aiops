import pytest

from app.core.project_context import resolve_project_id
from app.core.projects import ProjectConfig, ProjectsConfig, reset_projects_config
from app.schemas.response import APIError


@pytest.fixture(autouse=True)
def _mock_projects(monkeypatch):
    """注入测试用项目配置。"""
    import app.core.projects as mod
    reset_projects_config()
    test_config = ProjectsConfig(
        default_project="test-proj",
        projects={
            "test-proj": ProjectConfig(name="Test"),
            "other-proj": ProjectConfig(name="Other"),
            "disabled-proj": ProjectConfig(name="Disabled", enabled=False),
        },
    )
    monkeypatch.setattr(mod, "_config", test_config)
    yield
    reset_projects_config()


def test_missing_header_raises_40004():
    with pytest.raises(APIError) as e:
        resolve_project_id(None)
    assert e.value.code == 40004
    assert e.value.message == "项目不存在"


def test_empty_header_raises_40004():
    with pytest.raises(APIError) as e:
        resolve_project_id("")
    assert e.value.code == 40004
    assert e.value.message == "项目不存在"


def test_valid_project_passes():
    assert resolve_project_id("other-proj") == "other-proj"


def test_unknown_project_raises_40004():
    with pytest.raises(APIError) as e:
        resolve_project_id("nonexistent")
    assert e.value.code == 40004


def test_disabled_project_raises_40004():
    with pytest.raises(APIError) as e:
        resolve_project_id("disabled-proj")
    assert e.value.code == 40004
    assert e.value.message == "项目不存在"
