"""X-Project-Id 项目上下文依赖：业务接口必须显式提供。"""
from fastapi import Header

from app.core.constants import ErrorCode
from app.core.logging import set_project_id
from app.core.projects import get_projects_config
from app.schemas.response import APIError


def resolve_project_id(raw: str | None) -> str:
    """解析项目 id：业务上下文缺失或未知均视为项目不存在。"""
    config = get_projects_config()
    if not raw:
        raise APIError(ErrorCode.NOT_FOUND, "项目不存在")
    project = config.projects.get(raw)
    if project is None or not project.enabled:
        raise APIError(ErrorCode.NOT_FOUND, "项目不存在")
    return raw


async def get_project_id(
    x_project_id: str | None = Header(default=None),
) -> str:
    """FastAPI 依赖：解析必填 X-Project-Id。"""
    pid = resolve_project_id(x_project_id)
    set_project_id(pid)
    return pid
