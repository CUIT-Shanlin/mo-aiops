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
    assert e.value.code == 40001


def test_token_without_exp_rejected():
    # 不带 exp 的 token 永不过期，必须拒绝（require exp）
    token = jwt.encode({"sub": "u1", "role": "admin"}, SECRET, algorithm="HS256")
    with pytest.raises(APIError) as e:
        decode_and_verify(token, SECRET, "HS256")
    assert e.value.code == 40001


def test_non_admin_forbidden():
    with pytest.raises(APIError) as e:
        decode_and_verify(_token(role="user"), SECRET, "HS256")
    assert e.value.code == 40003


def test_garbage_token():
    with pytest.raises(APIError) as e:
        decode_and_verify("not-a-token", SECRET, "HS256")
    assert e.value.code == 40001
