"""X-Project-Id 项目上下文依赖：可选头 + 默认回退（配置文件驱动）。"""
from fastapi import Header

from app.core.constants import ErrorCode
from app.core.logging import set_project_id
from app.core.projects import get_projects_config
from app.schemas.response import APIError


def resolve_project_id(raw: str | None) -> str:
    """解析项目 id：有值校验是否在配置内，无值用默认。"""
    config = get_projects_config()
    if not raw:
        return config.default_project
    if raw not in config.projects:
        raise APIError(ErrorCode.NOT_FOUND, "项目不存在")
    return raw


async def get_project_id(
    x_project_id: str | None = Header(default=None),
) -> str:
    """FastAPI 依赖：解析 X-Project-Id，可选，缺失用默认项目。"""
    pid = resolve_project_id(x_project_id)
    set_project_id(pid)
    return pid
