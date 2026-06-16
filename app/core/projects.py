"""项目配置加载器：从 YAML 文件读取项目定义，支持 env 变量插值。"""
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class ProjectConfig(BaseModel):
    """单个被监控项目配置。"""

    name: str
    metric_profile: str = "java"
    datasources: dict[str, Any] = {}


class ProjectsConfig(BaseModel):
    """全部项目配置。"""

    default_project: str
    projects: dict[str, ProjectConfig]


_ENV_PATTERN = re.compile(r"\$\{(\w+)(?::-(.*?))?\}")


def _expand_env(value: Any) -> Any:
    """递归替换 ${VAR:-default} 占位符。"""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            return os.environ.get(m.group(1), m.group(2) or "")
        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(i) for i in value]
    return value


_config: ProjectsConfig | None = None


def load_projects_config(path: str | Path | None = None) -> ProjectsConfig:
    """加载并缓存项目配置（进程单例）。"""
    global _config
    if _config is not None:
        return _config
    if path is None:
        path = Path("config/projects.yaml")
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


def reset_projects_config() -> None:
    """测试用：重置缓存。"""
    global _config
    _config = None
