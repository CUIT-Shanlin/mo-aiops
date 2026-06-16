from app.core.constants import ErrorCode
from app.schemas.response import APIError, ApiResponse, ErrorResponse, Page, success


def test_success_wrapper():
    assert success({"a": 1}) == {"code": 0, "message": "success", "data": {"a": 1}}


def test_api_response_model():
    resp = ApiResponse[dict](data={"a": 1})
    assert resp.code == 0
    assert resp.message == "success"
    assert resp.model_dump() == {"code": 0, "message": "success", "data": {"a": 1}}


def test_error_response_model():
    err = ErrorResponse(code=40001, message="Token 已过期")
    assert err.model_dump() == {"code": 40001, "message": "Token 已过期", "data": None}


def test_api_error_fields():
    err = APIError(code=ErrorCode.NOT_FOUND, message="bad", data={"x": 1})
    assert err.code == 40004
    assert err.message == "bad"
    assert err.data == {"x": 1}
    assert err.http_status == 200


def test_page_model():
    p = Page(total=10, page=1, pageSize=5, items=[1, 2, 3])
    assert p.model_dump() == {"total": 10, "page": 1, "pageSize": 5, "items": [1, 2, 3]}
