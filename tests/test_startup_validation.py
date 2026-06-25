from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.core.config import get_settings
from app.core.projects import ProjectConfig, ProjectsConfig
from app.providers.base import BaseProvider, ProviderConfigurationError
from app.providers.health import validate_configured_providers


class _DummyProvider(BaseProvider):
    CONFIG_CLASS = None

    def __init__(self, project_id: str, datasource_type: str, result=None, error=None):
        super().__init__(project_id, datasource_type, config=None)  # type: ignore[arg-type]
        self._result = result
        self._error = error
        self.closed = False

    async def validate_scopes(self) -> dict[str, bool | str]:
        if self._error is not None:
            raise self._error
        if asyncio.iscoroutine(self._result):
            return await self._result
        return self._result or {"connectivity": True}

    async def close(self) -> None:
        self.closed = True


class _CloseFailureProvider(_DummyProvider):
    async def close(self) -> None:
        self.closed = True
        raise RuntimeError("close-secret")


class _SlowProvider(_DummyProvider):
    async def validate_scopes(self) -> dict[str, bool | str]:
        await asyncio.sleep(0.05)
        return {"connectivity": True}


@pytest.mark.asyncio
async def test_validate_configured_providers_collects_results_warns_and_closes(
    monkeypatch, caplog
):
    success_provider = _DummyProvider("p1", "prometheus", result={"connectivity": True})
    failure_provider = _DummyProvider("p1", "loki", error=RuntimeError("secret-token"))

    monkeypatch.setattr(
        "app.providers.health.create_project_providers",
        lambda *_args, **_kwargs: {
            "prometheus": success_provider,
            "loki": failure_provider,
        },
    )

    config = ProjectsConfig(
        default_project="p1",
        projects={"p1": ProjectConfig(name="P1")},
    )

    caplog.set_level(logging.WARNING, logger="app.providers.health")
    result = await validate_configured_providers(config, timeout_seconds=0.1)

    assert result == {
        "p1": {
            "prometheus": {"connectivity": True},
            "loki": {"connectivity": False, "error": "RuntimeError"},
        }
    }
    assert success_provider.closed is True
    assert failure_provider.closed is True
    warning_text = caplog.text
    assert "provider startup validation failed" in warning_text
    assert "project=p1" in warning_text
    assert "datasource=loki" in warning_text
    assert "error=RuntimeError" in warning_text
    assert "secret-token" not in warning_text


@pytest.mark.asyncio
async def test_validate_configured_providers_ignores_close_failure(monkeypatch):
    provider = _CloseFailureProvider("p1", "prometheus", result={"connectivity": True})

    monkeypatch.setattr(
        "app.providers.health.create_project_providers",
        lambda *_args, **_kwargs: {"prometheus": provider},
    )

    config = ProjectsConfig(
        default_project="p1",
        projects={"p1": ProjectConfig(name="P1")},
    )

    result = await validate_configured_providers(config, timeout_seconds=0.1)

    assert result == {"p1": {"prometheus": {"connectivity": True}}}
    assert provider.closed is True


@pytest.mark.asyncio
async def test_validate_configured_providers_skips_disabled_projects_and_keeps_empty_enabled(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.providers.health.create_project_providers",
        lambda project_id, _project: {"prometheus": _DummyProvider(project_id, "prometheus")}
        if project_id == "enabled-with-provider"
        else {},
    )

    config = ProjectsConfig(
        default_project="enabled-with-provider",
        projects={
            "enabled-with-provider": ProjectConfig(name="Enabled"),
            "enabled-empty": ProjectConfig(name="Enabled Empty"),
            "disabled": ProjectConfig(name="Disabled", enabled=False),
        },
    )

    result = await validate_configured_providers(config)

    assert result == {
        "enabled-with-provider": {"prometheus": {"connectivity": True}},
        "enabled-empty": {},
    }


@pytest.mark.asyncio
async def test_validate_configured_providers_construction_failure_warns_and_does_not_block_others(
    monkeypatch, caplog
):
    def _factory(project_id: str, _project: ProjectConfig):
        if project_id == "broken":
            raise ProviderConfigurationError("invalid provider config secret-password")
        return {"prometheus": _DummyProvider(project_id, "prometheus")}

    monkeypatch.setattr("app.providers.health.create_project_providers", _factory)

    config = ProjectsConfig(
        default_project="good",
        projects={
            "good": ProjectConfig(name="Good"),
            "broken": ProjectConfig(name="Broken"),
        },
    )

    caplog.set_level(logging.WARNING, logger="app.providers.health")
    result = await validate_configured_providers(config)

    assert result["good"] == {"prometheus": {"connectivity": True}}
    assert result["broken"] == {
        "__error__": {"connectivity": False, "error": "ProviderConfigurationError"}
    }
    assert "secret-password" not in str(result)
    warning_text = caplog.text
    assert "provider startup construction failed" in warning_text
    assert "project=broken" in warning_text
    assert "error=ProviderConfigurationError" in warning_text
    assert "secret-password" not in warning_text


@pytest.mark.asyncio
async def test_validate_configured_providers_warns_on_failed_connectivity_result(
    monkeypatch, caplog
):
    provider = _DummyProvider(
        "p1",
        "tempo",
        result={"connectivity": False, "error": "ready check failed secret-token"},
    )

    monkeypatch.setattr(
        "app.providers.health.create_project_providers",
        lambda *_args, **_kwargs: {"tempo": provider},
    )

    config = ProjectsConfig(
        default_project="p1",
        projects={"p1": ProjectConfig(name="P1")},
    )

    caplog.set_level(logging.WARNING, logger="app.providers.health")
    result = await validate_configured_providers(config, timeout_seconds=0.1)

    assert result == {
        "p1": {
            "tempo": {
                "connectivity": False,
                "error": "ready check failed secret-token",
            }
        }
    }
    warning_text = caplog.text
    assert "provider startup validation failed" in warning_text
    assert "project=p1" in warning_text
    assert "datasource=tempo" in warning_text
    assert "ready check failed" in warning_text
    assert "secret-token" not in warning_text


@pytest.mark.asyncio
async def test_validate_configured_providers_warns_on_string_connectivity_failure(
    monkeypatch, caplog
):
    provider = _DummyProvider(
        "p1",
        "prometheus",
        result={"connectivity": "HTTP 500 secret-token"},
    )

    monkeypatch.setattr(
        "app.providers.health.create_project_providers",
        lambda *_args, **_kwargs: {"prometheus": provider},
    )

    config = ProjectsConfig(
        default_project="p1",
        projects={"p1": ProjectConfig(name="P1")},
    )

    caplog.set_level(logging.WARNING, logger="app.providers.health")
    result = await validate_configured_providers(config, timeout_seconds=0.1)

    assert result == {
        "p1": {"prometheus": {"connectivity": "HTTP 500 secret-token"}}
    }
    warning_text = caplog.text
    assert "provider startup validation failed" in warning_text
    assert "project=p1" in warning_text
    assert "datasource=prometheus" in warning_text
    assert "HTTP 500" in warning_text
    assert "secret-token" not in warning_text


@pytest.mark.asyncio
async def test_validate_configured_providers_runs_across_projects_in_parallel(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.providers.health.create_project_providers",
        lambda project_id, _project: {
            "prometheus": _SlowProvider(project_id, "prometheus")
        },
    )

    config = ProjectsConfig(
        default_project="p1",
        projects={
            "p1": ProjectConfig(name="P1"),
            "p2": ProjectConfig(name="P2"),
        },
    )

    start = time.perf_counter()
    result = await validate_configured_providers(config, timeout_seconds=0.2)
    elapsed = time.perf_counter() - start

    assert result == {
        "p1": {"prometheus": {"connectivity": True}},
        "p2": {"prometheus": {"connectivity": True}},
    }
    assert elapsed < 0.13


def test_settings_can_disable_startup_provider_validation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("STARTUP_PROVIDER_VALIDATION", "false")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.startup_provider_validation is False


@pytest.mark.asyncio
async def test_lifespan_sets_projects_config_and_skips_validation_when_disabled(
    monkeypatch, tmp_path
):
    from app.main import get_app

    path = tmp_path / "projects.yaml"
    path.write_text(
        """
default_project: demo
projects:
  demo:
    name: Demo
""".strip()
    )

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///tmp/test.db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(path))
    monkeypatch.setenv("STARTUP_PROVIDER_VALIDATION", "false")
    monkeypatch.setenv("STARTUP_INGEST_ENABLED", "false")
    get_settings.cache_clear()

    called = False

    async def _unexpected_validate(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"demo": {"prometheus": {"connectivity": True}}}

    monkeypatch.setattr("app.main.ping_db", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.ping_redis", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.run_upgrade_head", lambda: None)
    monkeypatch.setattr("app.main.validate_configured_providers", _unexpected_validate)
    class _Engine:
        async def dispose(self) -> None:
            return None

    monkeypatch.setattr("app.main.create_engine", lambda *_args, **_kwargs: _Engine())
    monkeypatch.setattr("app.main.make_sessionmaker", lambda *_args, **_kwargs: object())

    class _Redis:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("app.main.create_redis", lambda *_args, **_kwargs: _Redis())

    app = get_app()
    async with app.router.lifespan_context(app):
        assert app.state.projects_config.default_project == "demo"
        assert app.state.provider_health == {}

    assert called is False


@pytest.mark.asyncio
async def test_lifespan_leaves_collector_disabled_by_default(monkeypatch, tmp_path):
    from app.main import get_app

    path = tmp_path / "projects.yaml"
    path.write_text(
        """
default_project: demo
projects:
  demo:
    name: Demo
""".strip()
    )

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///tmp/test.db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(path))
    monkeypatch.setenv("STARTUP_PROVIDER_VALIDATION", "false")
    monkeypatch.delenv("STARTUP_COLLECTOR_ENABLED", raising=False)
    monkeypatch.setenv("STARTUP_INGEST_ENABLED", "false")
    get_settings.cache_clear()

    monkeypatch.setattr("app.main.ping_db", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.ping_redis", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.run_upgrade_head", lambda: None)

    class _Engine:
        async def dispose(self) -> None:
            return None

    monkeypatch.setattr("app.main.create_engine", lambda *_args, **_kwargs: _Engine())
    monkeypatch.setattr("app.main.make_sessionmaker", lambda *_args, **_kwargs: object())

    class _Redis:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("app.main.create_redis", lambda *_args, **_kwargs: _Redis())

    app = get_app()
    async with app.router.lifespan_context(app):
        assert app.state.collector_scheduler is None
        assert app.state.collector_task is None


@pytest.mark.asyncio
async def test_lifespan_exposes_k8s_provider_factory(monkeypatch, tmp_path):
    from app.main import get_app

    path = tmp_path / "projects.yaml"
    path.write_text(
        """
default_project: demo
projects:
  demo:
    name: Demo
    datasources:
      kubernetes:
        mode: kubeconfig
        kubeconfig_path: ~/.kube/config
        namespaces: ["mochat"]
        verify_ssl: false
""".strip()
    )

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///tmp/test.db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(path))
    monkeypatch.setenv("STARTUP_PROVIDER_VALIDATION", "false")
    monkeypatch.setenv("STARTUP_COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("STARTUP_INGEST_ENABLED", "false")
    get_settings.cache_clear()

    monkeypatch.setattr("app.main.ping_db", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.ping_redis", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.run_upgrade_head", lambda: None)

    class _Engine:
        async def dispose(self) -> None:
            return None

    monkeypatch.setattr("app.main.create_engine", lambda *_args, **_kwargs: _Engine())
    monkeypatch.setattr("app.main.make_sessionmaker", lambda *_args, **_kwargs: object())

    class _Redis:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("app.main.create_redis", lambda *_args, **_kwargs: _Redis())

    created = []

    class _Provider:
        def __init__(self, project_id: str, datasource_type: str):
            self.project_id = project_id
            self.datasource_type = datasource_type
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    def _create_provider(project_id, datasource_type, _config):
        provider = _Provider(project_id, datasource_type)
        created.append(provider)
        return provider

    monkeypatch.setattr("app.main.create_provider", _create_provider)

    app = get_app()
    async with app.router.lifespan_context(app):
        factory = app.state.create_k8s_provider
        project = app.state.projects_config.projects["demo"]
        provider = factory("demo", project)
        same_provider = factory("demo", project)

    assert provider.project_id == "demo"
    assert provider.datasource_type == "kubernetes"
    assert same_provider is provider
    assert created == [provider]
    assert provider.closed is True


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_collector_when_enabled(monkeypatch, tmp_path):
    from app.main import get_app

    path = tmp_path / "projects.yaml"
    path.write_text(
        """
default_project: demo
projects:
  demo:
    name: Demo
""".strip()
    )

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///tmp/test.db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(path))
    monkeypatch.setenv("STARTUP_PROVIDER_VALIDATION", "false")
    monkeypatch.setenv("STARTUP_COLLECTOR_ENABLED", "true")
    monkeypatch.setenv("STARTUP_INGEST_ENABLED", "false")
    get_settings.cache_clear()

    monkeypatch.setattr("app.main.ping_db", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.ping_redis", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.run_upgrade_head", lambda: None)

    class _Engine:
        async def dispose(self) -> None:
            return None

    monkeypatch.setattr("app.main.create_engine", lambda *_args, **_kwargs: _Engine())
    monkeypatch.setattr("app.main.make_sessionmaker", lambda *_args, **_kwargs: object())

    class _Redis:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("app.main.create_redis", lambda *_args, **_kwargs: _Redis())

    started = asyncio.Event()
    stopped = False
    instances = []

    class _FakeScheduler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            instances.append(self)

        async def run_forever(self):
            started.set()
            await asyncio.Event().wait()

        async def stop(self):
            nonlocal stopped
            stopped = True

    monkeypatch.setattr("app.main.CollectorScheduler", _FakeScheduler)

    app = get_app()
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(started.wait(), timeout=1)
        assert instances[0].kwargs["projects_config"].default_project == "demo"
        assert app.state.collector_task is not None

    assert stopped is True


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_ingest_consumer_by_default(
    monkeypatch,
    tmp_path,
):
    from app.main import get_app

    path = tmp_path / "projects.yaml"
    path.write_text(
        """
default_project: demo
projects:
  demo:
    name: Demo
""".strip()
    )

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///tmp/test.db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(path))
    monkeypatch.setenv("STARTUP_PROVIDER_VALIDATION", "false")
    monkeypatch.delenv("STARTUP_INGEST_ENABLED", raising=False)
    get_settings.cache_clear()

    monkeypatch.setattr("app.main.ping_db", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.ping_redis", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.run_upgrade_head", lambda: None)

    class _Engine:
        async def dispose(self) -> None:
            return None

    monkeypatch.setattr("app.main.create_engine", lambda *_args, **_kwargs: _Engine())
    monkeypatch.setattr("app.main.make_sessionmaker", lambda *_args, **_kwargs: object())

    class _Redis:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("app.main.create_redis", lambda *_args, **_kwargs: _Redis())

    started = asyncio.Event()
    stopped = False
    instances = []

    class _FakeIngestConsumer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            instances.append(self)

        async def run_forever(self):
            started.set()
            await asyncio.Event().wait()

        async def stop(self):
            nonlocal stopped
            stopped = True

    monkeypatch.setattr("app.main.IngestConsumer", _FakeIngestConsumer)

    app = get_app()
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(started.wait(), timeout=1)
        assert instances[0].kwargs["projects_config"].default_project == "demo"
        assert app.state.ingest_task is not None

    assert stopped is True
