"""redis.asyncio 客户端（固定 DB 1）。"""
from fastapi import Request
from redis.asyncio import Redis, from_url


def create_redis(redis_url: str, db: int = 1) -> Redis:
    """创建 async redis 客户端，decode_responses=True。

    socket_keepalive + health_check_interval 防止长连接被 NAT/防火墙静默掐断
    后 brpop 永久卡住或抛 TimeoutError 导致后台任务崩溃。
    """
    return from_url(
        redis_url,
        db=db,
        decode_responses=True,
        socket_keepalive=True,
        health_check_interval=30,
        socket_connect_timeout=5,
        socket_timeout=10,
    )


async def get_redis(request: Request) -> Redis:
    """FastAPI 依赖：取 app.state.redis。"""
    return request.app.state.redis


async def ping_redis(client: Redis) -> bool:
    """探活：PING，异常返回 False。"""
    try:
        return bool(await client.ping())
    except Exception:
        return False
