import asyncio
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.migrate import run_upgrade_head


MIGRATION = Path("app/db/migrations/versions/0005_create_m5_saved_queries.py")


async def test_m5_saved_queries_migration_file_exists_and_mentions_table():
    assert MIGRATION.exists()
    content = MIGRATION.read_text()
    assert 'revision = "0005"' in content
    assert 'down_revision = "0004"' in content
    assert "saved_queries" in content
    assert "ix_saved_queries_project_id" in content
    assert "ix_saved_queries_project_created" in content
    assert "uq_saved_queries_project_name" in content


async def test_m5_upgrade_head_creates_saved_queries_table():
    await asyncio.to_thread(run_upgrade_head)
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT to_regclass('public.saved_queries')"))
        assert result.scalar() == "saved_queries"
        result = await conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'public.saved_queries'::regclass "
                "AND conname = 'uq_saved_queries_project_name'"
            )
        )
        assert result.scalar() == "uq_saved_queries_project_name"
    await engine.dispose()
