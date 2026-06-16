from __future__ import annotations

from importlib import import_module
from typing import Any, Mapping

from pydantic import BaseModel, ValidationError

from app.core.projects import ProjectConfig
from app.providers.base import BaseProvider, ProviderConfigurationError


def _class_name(datasource_type: str) -> str:
    parts = [part for part in datasource_type.split("_") if part]
    return "".join(part.capitalize() for part in parts) + "Provider"


def get_provider_class(datasource_type: str) -> type[BaseProvider]:
    module_name = f"app.providers.{datasource_type}"
    class_name = _class_name(datasource_type)
    try:
        module = import_module(module_name)
        provider_class = getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise ProviderConfigurationError(
            f"unknown provider type {datasource_type}"
        ) from exc

    if not isinstance(provider_class, type) or not issubclass(provider_class, BaseProvider):
        raise ProviderConfigurationError(
            f"invalid provider class for type {datasource_type}"
        )
    return provider_class


def create_provider(
    project_id: str,
    datasource_type: str,
    raw_config: Mapping[str, Any] | BaseModel,
) -> BaseProvider:
    provider_class = get_provider_class(datasource_type)
    config_class = getattr(provider_class, "CONFIG_CLASS", None)
    if config_class is None or not isinstance(config_class, type) or not issubclass(
        config_class, BaseModel
    ):
        raise ProviderConfigurationError(
            f"provider {datasource_type} missing CONFIG_CLASS"
        )

    try:
        if isinstance(raw_config, config_class):
            config = raw_config
        else:
            config = config_class.model_validate(raw_config)
    except ValidationError:
        raise ProviderConfigurationError(
            f"invalid {datasource_type} provider config"
        ) from None

    return provider_class(
        project_id=project_id,
        datasource_type=datasource_type,
        config=config,
    )


def create_project_providers(
    project_id: str,
    project_config: ProjectConfig,
) -> dict[str, BaseProvider]:
    providers: dict[str, BaseProvider] = {}
    for datasource_type, config in project_config.datasource_configs.items():
        providers[datasource_type] = create_provider(
            project_id=project_id,
            datasource_type=datasource_type,
            raw_config=config,
        )
    return providers
