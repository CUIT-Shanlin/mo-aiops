import time

import jwt
import pytest

from app.core.security import CurrentUser, decode_and_verify
from app.schemas.response import APIError

SECRET = "secret"


def _token(role="admin", exp_delta=3600, sub="u1"):
    payload = {"sub": sub, "role": role, "exp": int(time.time()) + exp_delta}
    return jwt.encode(payload, SECRET, algorithm="HS256")


def test_valid_admin_token():
    user = decode_and_verify(_token(), SECRET, "HS256")
    assert isinstance(user, CurrentUser)
    assert user.role == "admin"
    assert user.user_id == "u1"


def test_expired_token():
    with pytest.raises(APIError) as e:
        decode_and_verify(_token(exp_delta=-10), SECRET, "HS256")
    assert e.value.code == 4010


def test_non_admin_forbidden():
    with pytest.raises(APIError) as e:
        decode_and_verify(_token(role="user"), SECRET, "HS256")
    assert e.value.code == 4030


def test_garbage_token():
    with pytest.raises(APIError) as e:
        decode_and_verify("not-a-token", SECRET, "HS256")
    assert e.value.code == 4010
