from pydantic import BaseModel
import pytest

from app.core.projects import ProjectConfig, PrometheusDatasourceConfig
from app.providers.base import ProviderConfigurationError
from app.providers.factory import (
    create_project_providers,
    create_provider,
    get_provider_class,
)
from app.providers.prometheus import PrometheusProvider


def test_get_provider_class_returns_prometheus_provider():
    assert get_provider_class("prometheus") is PrometheusProvider


def test_create_provider_validates_and_instantiates():
    provider = create_provider(
        project_id="demo",
        datasource_type="prometheus",
        raw_config={
            "base_url": "http://prometheus:9090",
            "username": "admin",
            "password": "secret-password",
        },
    )

    assert isinstance(provider, PrometheusProvider)
    assert provider.config.base_url == "http://prometheus:9090"
    assert "secret-password" not in repr(provider.config)


def test_create_provider_accepts_validated_config_instance():
    config = PrometheusDatasourceConfig(base_url="http://prometheus:9090")

    provider = create_provider(
        project_id="demo",
        datasource_type="prometheus",
        raw_config=config,
    )

    assert provider.config is config


def test_create_provider_unknown_type_raises_configuration_error():
    with pytest.raises(ProviderConfigurationError, match="unknown provider type"):
        create_provider(
            project_id="demo",
            datasource_type="unknown_provider",
            raw_config={},
        )


def test_create_project_providers_uses_only_supported_validated_datasources():
    project = ProjectConfig(
        name="Demo",
        datasources={
            "prometheus": {"base_url": "http://prometheus:9090"},
            "custom_source": {"base_url": "http://ignored"},
        },
    )

    providers = create_project_providers("demo", project)

    assert list(providers) == ["prometheus"]
    assert isinstance(providers["prometheus"], PrometheusProvider)


def test_create_provider_invalid_config_redacts_secret_values():
    with pytest.raises(ProviderConfigurationError) as exc_info:
        create_provider(
            project_id="demo",
            datasource_type="loki",
            raw_config={
                "base_url": "http://loki:3100",
                "auth_type": "Basic",
                "username": "admin",
                "password": "secret-password",
                "tenant_id": "secret-tenant",
                "verify_ssl": "definitely-not-bool",
            },
        )

    text = str(exc_info.value)
    assert "invalid loki provider config" in text
    assert "secret-password" not in text
    assert "secret-tenant" not in text


def test_get_provider_class_rejects_non_provider_subclass(monkeypatch):
    class NotAProvider(BaseModel):
        pass

    class _Module:
        PrometheusProvider = NotAProvider

    monkeypatch.setattr("app.providers.factory.import_module", lambda _: _Module())

    with pytest.raises(ProviderConfigurationError, match="invalid provider class"):
        get_provider_class("prometheus")
