import uuid


async def test_whoami_ok(client, make_jwt):
    headers = {
        "Authorization": f"Bearer {make_jwt(role='admin')}",
        "X-Project-Id": str(uuid.uuid4()),
    }
    resp = await client.get("/api/whoami", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["role"] == "admin"


async def test_whoami_missing_token(client):
    resp = await client.get("/api/whoami", headers={"X-Project-Id": str(uuid.uuid4())})
    assert resp.json()["code"] == 4010


async def test_whoami_missing_project(client, make_jwt):
    headers = {"Authorization": f"Bearer {make_jwt(role='admin')}"}
    resp = await client.get("/api/whoami", headers=headers)
    assert resp.json()["code"] == 4001


async def test_whoami_non_admin(client, make_jwt):
    headers = {
        "Authorization": f"Bearer {make_jwt(role='user')}",
        "X-Project-Id": str(uuid.uuid4()),
    }
    resp = await client.get("/api/whoami", headers=headers)
    assert resp.json()["code"] == 4030
