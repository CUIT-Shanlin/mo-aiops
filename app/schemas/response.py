"""统一响应包装与业务异常。

- ApiResponse[T] / ErrorResponse：类型化信封，供路由声明 response_model，
  让 FastAPI 自动生成 OpenAPI schema（契约即文档，AGENTS.md §9）。
- success()：便捷构造成功 body。
- APIError：业务异常，HTTP 200 + body 内携带业务 code。
"""
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """成功响应信封：{code:0, data}。路由用 response_model=ApiResponse[XxxData]。"""

    code: int = 0
    data: T


class ErrorResponse(BaseModel):
    """失败响应信封：{code, message, detail}。"""

    code: int
    message: str
    detail: Any = None


class APIError(Exception):
    """业务异常：HTTP 200 + body 内携带业务 code。"""

    def __init__(
        self, code: int, message: str, detail: Any = None, http_status: int = 200
    ) -> None:
        self.code = int(code)
        self.message = message
        self.detail = detail
        self.http_status = http_status
        super().__init__(message)


def success(data: Any) -> dict:
    """成功响应包装（便捷构造；需 OpenAPI schema 时用 ApiResponse[T]）。"""
    return {"code": 0, "data": data}


class Page(BaseModel, Generic[T]):
    """分页响应模型。"""

    total: int
    page: int
    size: int
    items: list[T]
