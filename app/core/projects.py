"""项目配置加载器：从 YAML 文件读取项目定义，支持 env 变量插值。"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, TypeAlias, cast

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.metrics_profile import get_profile
from app.metrics_profile.base import UnknownProfileError


class LokiAuthType(str, Enum):
    NO_AUTH = "NoAuth"
    BASIC = "Basic"
    X_SCOPE_ORG_ID = "X-Scope-OrgID"


class KubernetesMode(str, Enum):
    IN_CLUSTER = "in_cluster"
    KUBECONFIG = "kubeconfig"
    TOKEN = "token"


class PrometheusDatasourceConfig(BaseModel):
    model_config = {"extra": "forbid"}

    base_url: str
    username: str | None = None
    password: str | None = Field(default=None, repr=False, exclude=True)
    verify_ssl: bool = True


class LokiDatasourceConfig(BaseModel):
    model_config = {"extra": "forbid"}

    base_url: str
    auth_type: LokiAuthType = LokiAuthType.NO_AUTH
    username: str | None = None
    password: str | None = Field(default=None, repr=False, exclude=True)
    tenant_id: str | None = Field(default=None, repr=False, exclude=True)
    verify_ssl: bool = True


class TempoDatasourceConfig(BaseModel):
    model_config = {"extra": "forbid"}

    base_url: str
    username: str | None = None
    password: str | None = Field(default=None, repr=False, exclude=True)
    verify_ssl: bool = True


class KubernetesDatasourceConfig(BaseModel):
    model_config = {"extra": "forbid"}

    mode: KubernetesMode
    api_server: str | None = None
    token: str | None = Field(default=None, repr=False, exclude=True)
    kubeconfig_path: str | None = None
    namespaces: list[str] = Field(default_factory=list)
    verify_ssl: bool = True

    @model_validator(mode="after")
    def _validate_mode(self) -> "KubernetesDatasourceConfig":
        if self.mode is KubernetesMode.KUBECONFIG and not self.kubeconfig_path:
            raise ValueError("kubeconfig mode requires kubeconfig_path")
        if self.mode is KubernetesMode.TOKEN and not self.api_server:
            raise ValueError("token mode requires api_server")
        return self


DatasourceConfig: TypeAlias = (
    PrometheusDatasourceConfig
    | LokiDatasourceConfig
    | TempoDatasourceConfig
    | KubernetesDatasourceConfig
)


_SUPPORTED_DATASOURCE_MODELS: dict[str, type[BaseModel]] = {
    "prometheus": PrometheusDatasourceConfig,
    "loki": LokiDatasourceConfig,
    "tempo": TempoDatasourceConfig,
    "kubernetes": KubernetesDatasourceConfig,
}


class ProjectConfig(BaseModel):
    """单个被监控项目配置。"""

    name: str
    metric_profile: str = "java"
    enabled: bool = True
    datasources: dict[str, Any] = Field(default_factory=dict, repr=False, exclude=True)
    datasource_configs: dict[str, DatasourceConfig] = Field(
        default_factory=dict, repr=False, exclude=True
    )

    @model_validator(mode="after")
    def _validate_and_expand(self) -> "ProjectConfig":
        try:
            get_profile(self.metric_profile)
        except UnknownProfileError as exc:
            raise ValueError(str(exc)) from exc

        validated_configs: dict[str, DatasourceConfig] = {}
        for datasource_name, raw_config in self.datasources.items():
            model = _SUPPORTED_DATASOURCE_MODELS.get(datasource_name)
            if model is None:
                continue
            try:
                validated_configs[datasource_name] = cast(
                    DatasourceConfig, model.model_validate(raw_config)
                )
            except ValidationError as exc:
                raise ValueError(
                    f"invalid {datasource_name} datasource config: {exc}"
                ) from exc

        self.datasource_configs = validated_configs
        return self


class ProjectsConfig(BaseModel):
    """全部项目配置。"""

    default_project: str
    projects: dict[str, ProjectConfig]

    @model_validator(mode="after")
    def _validate_default(self) -> "ProjectsConfig":
        if self.default_project not in self.projects:
            raise ValueError(
                f"default_project '{self.default_project}' not in projects: "
                f"{list(self.projects.keys())}"
            )
        return self


_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::-(.*?))?\}")


def _expand_env(value: Any) -> Any:
    """递归替换 ${VAR:-default} 占位符。"""

    if isinstance(value, str):

        def _replace(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), m.group(2) or "")

        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(i) for i in value]
    return value


_config: ProjectsConfig | None = None


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def load_projects_config(path: str | Path | None = None) -> ProjectsConfig:
    """加载并缓存项目配置（进程单例）。"""
    global _config
    if _config is not None:
        return _config
    if path is None:
        from app.core.config import get_settings

        path = Path(get_settings().projects_config_path)
    path = Path(path)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    with open(path) as f:
        raw = yaml.safe_load(f)
    expanded = _expand_env(raw)
    _config = ProjectsConfig(**expanded)
    return _config


def get_projects_config() -> ProjectsConfig:
    """获取已加载的项目配置（需先调用 load_projects_config）。"""
    if _config is None:
        return load_projects_config()
    return _config


def iter_enabled_projects(
    config: ProjectsConfig | None = None,
) -> Iterator[tuple[str, ProjectConfig]]:
    """遍历启用中的项目。"""
    current = config or get_projects_config()
    for project_id, project in current.projects.items():
        if project.enabled:
            yield project_id, project


def reset_projects_config() -> None:
    """测试用：重置缓存。"""
    global _config
    _config = None
