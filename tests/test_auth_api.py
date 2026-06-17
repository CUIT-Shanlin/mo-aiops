from datetime import UTC, datetime

import jwt

from app.core.config import get_settings


async def test_login_me_refresh_logout_contract(client, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "plain:secret")
    get_settings.cache_clear()

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "secret"},
    )

    assert login.status_code == 200
    body = login.json()
    assert body["code"] == 0
    token = body["data"]["token"]
    refresh_token = body["data"]["refreshToken"]
    assert body["data"]["user"] == {"id": "admin", "username": "admin", "role": "admin"}

    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.json()["data"] == {"id": "admin", "username": "admin", "role": "admin"}

    refreshed = await client.post(
        "/api/v1/auth/refresh",
        json={"refreshToken": refresh_token},
    )
    assert refreshed.json()["code"] == 0
    assert refreshed.json()["data"]["token"]

    logout = await client.post("/api/v1/auth/logout", headers=headers)
    assert logout.json() == {"code": 0, "message": "success", "data": {"success": True}}

    payload = jwt.decode(
        token,
        get_settings().jwt_secret,
        algorithms=[get_settings().jwt_algorithm],
    )
    assert payload["sub"] == "admin"
    assert payload["role"] == "admin"
    assert payload["typ"] == "access"
    assert datetime.fromtimestamp(payload["exp"], UTC) > datetime.now(UTC)


async def test_login_rejects_bad_credentials(client, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "plain:secret")
    get_settings.cache_clear()

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong"},
    )

    assert response.status_code == 200
    assert response.json() == {"code": 40001, "message": "用户名或密码错误", "data": None}


async def test_refresh_rejects_access_token(client, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "plain:secret")
    get_settings.cache_clear()

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "secret"},
    )
    token = login.json()["data"]["token"]

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refreshToken": token},
    )

    assert response.status_code == 200
    assert response.json()["code"] == 40001
