"""X-Project-Id 项目上下文依赖（M0 仅格式校验，库存在性校验留 M1）。"""
import uuid

from fastapi import Header

from app.core.constants import ErrorCode
from app.core.logging import set_project_id
from app.schemas.response import APIError


def resolve_project_id(raw: str | None) -> str:
    """校验 X-Project-Id 存在且为合法 UUID，否则抛 4001。"""
    if not raw:
        raise APIError(ErrorCode.INVALID_PROJECT, "missing or invalid project")
    try:
        uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        raise APIError(ErrorCode.INVALID_PROJECT, "missing or invalid project")
    return raw


async def get_project_id(
    x_project_id: str | None = Header(default=None),
) -> str:
    """FastAPI 依赖：解析并校验 X-Project-Id，注入日志 contextvar。"""
    pid = resolve_project_id(x_project_id)
    set_project_id(pid)
    return pid
