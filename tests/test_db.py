import os

import pytest

from app.core.db import create_engine, make_sessionmaker, ping_db


@pytest.fixture
def engine():
    url = os.environ.get("DATABASE_URL")
    if not url:
        from app.core.config import get_settings

        url = get_settings().database_url
    eng = create_engine(url)
    yield eng


async def test_ping_db_ok(engine):
    assert await ping_db(engine) is True


async def test_sessionmaker_yields_session(engine):
    sm = make_sessionmaker(engine)
    async with sm() as session:
        from sqlalchemy import text

        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
    await engine.dispose()
