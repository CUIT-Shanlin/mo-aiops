import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.migrate import run_upgrade_head


async def test_users_table_exists_after_migrations():
    await asyncio.to_thread(run_upgrade_head)
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT to_regclass('public.users')"))
        assert result.scalar() == "users"

        index_result = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'users'"
            )
        )
        index_names = {row[0] for row in index_result}
        assert "ix_users_project_id" in index_names
        assert "ix_users_project_status_risk" in index_names
        assert "ix_users_project_username" in index_names

        constraint_result = await conn.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'public.users'::regclass "
                "AND conname = 'uq_users_project_id_id'"
            )
        )
        assert constraint_result.scalar() == "uq_users_project_id_id"
    await engine.dispose()


async def test_users_unique_constraint_allows_same_id_in_different_projects():
    await asyncio.to_thread(run_upgrade_head)
    prod_project_id = "migration-prod"
    stage_project_id = "migration-stage"
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM users "
                    "WHERE project_id IN (:prod_project_id, :stage_project_id)"
                ),
                {
                    "prod_project_id": prod_project_id,
                    "stage_project_id": stage_project_id,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO users (project_id, id, username) "
                    "VALUES (:prod_project_id, 'admin', 'admin'), "
                    "(:stage_project_id, 'admin', 'admin')"
                ),
                {
                    "prod_project_id": prod_project_id,
                    "stage_project_id": stage_project_id,
                },
            )
            result = await conn.execute(
                text(
                    "SELECT count(*) FROM users "
                    "WHERE id = 'admin' "
                    "AND project_id IN (:prod_project_id, :stage_project_id)"
                ),
                {
                    "prod_project_id": prod_project_id,
                    "stage_project_id": stage_project_id,
                },
            )
            assert result.scalar_one() == 2
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM users "
                    "WHERE project_id IN (:prod_project_id, :stage_project_id)"
                ),
                {
                    "prod_project_id": prod_project_id,
                    "stage_project_id": stage_project_id,
                },
            )
        await engine.dispose()
