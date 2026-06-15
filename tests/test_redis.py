import os

import pytest

from app.core.redis import create_redis, ping_redis


@pytest.fixture
async def redis_client():
    url = os.environ.get("REDIS_URL")
    if not url:
        from app.core.config import get_settings

        url = get_settings().redis_url
    client = create_redis(url, db=1)
    yield client
    await client.aclose()


async def test_ping_redis_ok(redis_client):
    assert await ping_redis(redis_client) is True


async def test_set_get(redis_client):
    await redis_client.set("aiops:test:k", "v")
    assert await redis_client.get("aiops:test:k") == "v"
    await redis_client.delete("aiops:test:k")
