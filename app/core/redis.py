"""redis.asyncio 客户端（固定 DB 1）。"""
from fastapi import Request
from redis.asyncio import Redis, from_url


def create_redis(redis_url: str, db: int = 1) -> Redis:
    """创建 async redis 客户端，decode_responses=True。"""
    return from_url(redis_url, db=db, decode_responses=True)


async def get_redis(request: Request) -> Redis:
    """FastAPI 依赖：取 app.state.redis。"""
    return request.app.state.redis


async def ping_redis(client: Redis) -> bool:
    """探活：PING，异常返回 False。"""
    try:
        return bool(await client.ping())
    except Exception:
        return False
