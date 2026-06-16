import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import run_upgrade_head


async def test_upgrade_runs_without_error():
    """迁移幂等执行不报错；projects 表应不存在（已被 drop）。"""
    await asyncio.to_thread(run_upgrade_head)
    url = os.environ.get("DATABASE_URL")
    if not url:
        from app.core.config import get_settings
        url = get_settings().database_url
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT to_regclass('public.projects')"))
        assert result.scalar() is None
    await engine.dispose()
