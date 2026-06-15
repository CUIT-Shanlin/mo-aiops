import uuid

import pytest

from app.core.project_context import resolve_project_id
from app.schemas.response import APIError


def test_valid_uuid():
    pid = str(uuid.uuid4())
    assert resolve_project_id(pid) == pid


def test_missing_returns_4001():
    with pytest.raises(APIError) as e:
        resolve_project_id(None)
    assert e.value.code == 4001


def test_invalid_format_returns_4001():
    with pytest.raises(APIError) as e:
        resolve_project_id("not-a-uuid")
    assert e.value.code == 4001
