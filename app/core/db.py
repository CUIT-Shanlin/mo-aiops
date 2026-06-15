"""SQLAlchemy 2.0 async 引擎与会话。engine 在 lifespan 内创建，不放模块全局。"""
import json
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _json_serializer(obj: object) -> str:
    """JSONB 序列化：default=str 解决 datetime/UUID。"""
    return json.dumps(obj, default=str)


def create_engine(database_url: str) -> AsyncEngine:
    """创建 async 引擎（连接池 min5/max20 概念：pool_size5 + overflow15）。"""
    return create_async_engine(
        database_url,
        pool_size=5,
        max_overflow=15,
        pool_pre_ping=True,
        json_serializer=_json_serializer,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建 session 工厂。"""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：从 app.state.sessionmaker 取一个 session。"""
    sm: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sm() as session:
        yield session


async def ping_db(engine: AsyncEngine) -> bool:
    """探活：SELECT 1，异常返回 False。"""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
