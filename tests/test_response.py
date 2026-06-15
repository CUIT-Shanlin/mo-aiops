from app.core.constants import ErrorCode
from app.schemas.response import APIError, ApiResponse, ErrorResponse, Page, success


def test_success_wrapper():
    assert success({"a": 1}) == {"code": 0, "data": {"a": 1}}


def test_api_response_model():
    resp = ApiResponse[dict](data={"a": 1})
    assert resp.code == 0
    assert resp.model_dump() == {"code": 0, "data": {"a": 1}}


def test_error_response_model():
    err = ErrorResponse(code=4001, message="bad", detail=None)
    assert err.model_dump() == {"code": 4001, "message": "bad", "detail": None}


def test_api_error_fields():
    err = APIError(code=ErrorCode.INVALID_PROJECT, message="bad", detail={"x": 1})
    assert err.code == 4001
    assert err.message == "bad"
    assert err.detail == {"x": 1}
    assert err.http_status == 200


def test_page_model():
    p = Page(total=10, page=1, size=5, items=[1, 2, 3])
    assert p.model_dump() == {"total": 10, "page": 1, "size": 5, "items": [1, 2, 3]}
