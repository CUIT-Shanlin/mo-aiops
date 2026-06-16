"""PyJWT HS256 鉴权依赖（MVP 仅 admin）。"""
import jwt
from fastapi import Header
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.constants import ErrorCode
from app.schemas.response import APIError


class CurrentUser(BaseModel):
    """已鉴权用户身份。"""

    user_id: str | None
    role: str


def decode_and_verify(token: str, secret: str, algorithm: str) -> CurrentUser:
    """解码 JWT，校验签名/过期/角色。失败抛 APIError。"""
    try:
        payload = jwt.decode(
            token, secret, algorithms=[algorithm], options={"require": ["exp"]}
        )
    except jwt.ExpiredSignatureError:
        raise APIError(ErrorCode.UNAUTHORIZED, "Token 已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise APIError(ErrorCode.UNAUTHORIZED, "无效 Token")
    if payload.get("role") != "admin":
        raise APIError(ErrorCode.FORBIDDEN, "权限不足")
    return CurrentUser(user_id=payload.get("sub"), role=payload["role"])


async def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """FastAPI 依赖：从 Authorization: Bearer 取 token 并校验。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise APIError(ErrorCode.UNAUTHORIZED, "未登录")
    token = authorization.removeprefix("Bearer ").strip()
    settings = get_settings()
    return decode_and_verify(token, settings.jwt_secret, settings.jwt_algorithm)
