from __future__ import annotations

import hmac
import time
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.schemas.response import APIError, success

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

ACCESS_TTL_SECONDS = 3600
REFRESH_TTL_SECONDS = 86400
INVALID_CREDENTIALS_MESSAGE = "用户名或密码错误"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refreshToken: str = Field(min_length=1)


@router.post("/login")
async def login(payload: LoginRequest) -> dict[str, Any]:
    settings = get_settings()
    if payload.username != settings.admin_username or not _verify_password(
        payload.password,
        settings.admin_password_hash,
    ):
        raise APIError(ErrorCode.UNAUTHORIZED, INVALID_CREDENTIALS_MESSAGE)

    user = _admin_user(settings.admin_username)
    return success(
        {
            "token": _issue_token(settings.admin_username, ACCESS_TTL_SECONDS, "access"),
            "refreshToken": _issue_token(
                settings.admin_username,
                REFRESH_TTL_SECONDS,
                "refresh",
            ),
            "user": user,
        }
    )


@router.post("/logout")
async def logout(
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict[str, Any]:
    return success({"success": True})


@router.get("/me")
async def me(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict[str, Any]:
    username = current_user.user_id or get_settings().admin_username
    return success(_admin_user(username))


@router.post("/refresh")
async def refresh(payload: RefreshRequest) -> dict[str, Any]:
    settings = get_settings()
    try:
        decoded = jwt.decode(
            payload.refreshToken,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp"]},
        )
    except jwt.InvalidTokenError as exc:
        raise APIError(ErrorCode.UNAUTHORIZED, INVALID_CREDENTIALS_MESSAGE) from exc

    if decoded.get("role") != "admin" or decoded.get("typ") != "refresh":
        raise APIError(ErrorCode.UNAUTHORIZED, INVALID_CREDENTIALS_MESSAGE)

    username = str(decoded.get("sub") or settings.admin_username)
    return success({"token": _issue_token(username, ACCESS_TTL_SECONDS, "access")})


def _issue_token(subject: str, ttl_seconds: int, token_type: str) -> str:
    settings = get_settings()
    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "role": "admin",
            "typ": token_type,
            "iat": now,
            "exp": now + ttl_seconds,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _verify_password(password: str, stored_hash: str) -> bool:
    if stored_hash.startswith("plain:"):
        return hmac.compare_digest(password, stored_hash.removeprefix("plain:"))
    return False


def _admin_user(username: str) -> dict[str, str]:
    return {"id": username, "username": username, "role": "admin"}
