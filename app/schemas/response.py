"""统一响应包装与业务异常，对齐 AIOps_API_文档 §1.2。

- 成功：{code:0, message:"success", data}
- 错误：{code:4xxxx/5xxxx, message:str, data:null}
- 分页：data 内含 {total, page, pageSize, items}
"""
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """成功响应信封：{code:0, message:"success", data}。"""

    code: int = 0
    message: str = "success"
    data: T


class ErrorResponse(BaseModel):
    """失败响应信封：{code, message, data:null}。"""

    code: int
    message: str
    data: Any = None


class APIError(Exception):
    """业务异常：HTTP 200 + body 内携带业务 code。"""

    def __init__(
        self, code: int, message: str, data: Any = None, http_status: int = 200
    ) -> None:
        self.code = int(code)
        self.message = message
        self.data = data
        self.http_status = http_status
        super().__init__(message)


def success(data: Any, message: str = "success") -> dict:
    """成功响应包装。"""
    return {"code": 0, "message": message, "data": data}


class Page(BaseModel, Generic[T]):
    """分页响应模型（放在 data 字段内）。"""

    total: int
    page: int
    pageSize: int
    items: list[T]
