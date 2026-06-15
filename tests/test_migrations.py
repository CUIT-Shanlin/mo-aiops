import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import run_upgrade_head


async def test_upgrade_creates_projects_table():
    # run_upgrade_head 内部走 asyncio.run，必须丢到无 loop 的线程执行
    await asyncio.to_thread(run_upgrade_head)
    # DATABASE_URL 在纯 pytest 下不一定进 os.environ（pydantic 读 .env 进 Settings），
    # 因此 os.environ 优先、回退到 Settings，与 test_db.py 保持一致。
    url = os.environ.get("DATABASE_URL")
    if not url:
        from app.core.config import get_settings

        url = get_settings().database_url
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT to_regclass('public.projects')"))
        assert result.scalar() is not None
    await engine.dispose()
