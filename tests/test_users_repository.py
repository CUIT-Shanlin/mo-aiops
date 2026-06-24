from sqlalchemy import text

from app.repositories.users import UserCreate, UserRepository


async def _clean_users(app_instance, *project_ids: str) -> None:
    async with app_instance.state.sessionmaker() as session:
        for project_id in project_ids:
            await session.execute(
                text("DELETE FROM users WHERE project_id = :project_id"),
                {"project_id": project_id},
            )
        await session.commit()


async def test_user_repository_create_list_filters_stats_and_project_isolation(
    app_instance,
):
    prod_project_id = "repo-prod-filters"
    stage_project_id = "repo-stage-filters"
    await _clean_users(app_instance, prod_project_id, stage_project_id)
    try:
        async with app_instance.state.sessionmaker() as session:
            prod = UserRepository(session, prod_project_id)
            stage = UserRepository(session, stage_project_id)
            await prod.create(
                UserCreate(
                    id="1001",
                    username="admin",
                    email="admin@example.com",
                    online_status="online",
                    risk_level="high",
                )
            )
            await prod.create(
                UserCreate(
                    id="1002",
                    username="alice",
                    email="alice@example.com",
                    is_banned=True,
                    online_status="offline",
                    risk_level="low",
                )
            )
            await stage.create(
                UserCreate(
                    id="1001",
                    username="admin",
                    email="stage-admin@example.com",
                    online_status="online",
                    risk_level="high",
                )
            )
            await session.commit()

            all_prod = await prod.search(page=1, page_size=20)
            keyword_rows = await prod.search(keyword="ali", page=1, page_size=20)
            id_keyword_rows = await prod.search(keyword="1001", page=1, page_size=20)
            banned_rows = await prod.search(is_banned=True, page=1, page_size=20)
            online_rows = await prod.search(online_status="online", page=1, page_size=20)
            high_risk_rows = await prod.search(risk_level="high", page=1, page_size=20)
            stats = await prod.stats()
            stage_rows = await stage.search(page=1, page_size=20)

        assert all_prod.total == 2
        assert [row.username for row in all_prod.items] == ["admin", "alice"]
        assert keyword_rows.total == 1
        assert keyword_rows.items[0].username == "alice"
        assert id_keyword_rows.total == 1
        assert id_keyword_rows.items[0].id == "1001"
        assert banned_rows.total == 1
        assert banned_rows.items[0].username == "alice"
        assert online_rows.total == 1
        assert online_rows.items[0].username == "admin"
        assert high_risk_rows.total == 1
        assert high_risk_rows.items[0].username == "admin"
        assert stats == {"total": 2, "online": 1, "banned": 1, "highRisk": 1}
        assert stage_rows.total == 1
        assert stage_rows.items[0].email == "stage-admin@example.com"
    finally:
        await _clean_users(app_instance, prod_project_id, stage_project_id)


async def test_user_repository_ensure_admin_and_ban_unban(app_instance):
    project_id = "repo-prod-admin"
    await _clean_users(app_instance, project_id)
    try:
        async with app_instance.state.sessionmaker() as session:
            repo = UserRepository(session, project_id)
            admin = await repo.ensure_admin()
            same_admin = await repo.ensure_admin()
            custom_admin = await repo.ensure_admin("root")
            await repo.set_banned(admin.id, True)
            banned = await repo.get(admin.id)
            assert banned is not None
            assert banned.is_banned is True

            await repo.set_banned(admin.id, False)
            unbanned = await repo.get(admin.id)
            await session.commit()

        assert admin.id == "admin"
        assert admin.username == "admin"
        assert admin.online_status == "offline"
        assert admin.risk_level == "normal"
        assert admin.email is None
        assert admin.avatar is None
        assert admin.today_messages == 0
        assert same_admin.id == admin.id
        assert custom_admin.id == "root"
        assert custom_admin.username == "root"
        assert custom_admin.online_status == "offline"
        assert custom_admin.risk_level == "normal"
        assert custom_admin.email is None
        assert unbanned is not None
        assert unbanned.is_banned is False
    finally:
        await _clean_users(app_instance, project_id)


async def test_user_repository_get_and_ban_are_project_scoped_for_same_user_id(
    app_instance,
):
    prod_project_id = "repo-prod-admin-id"
    stage_project_id = "repo-stage-admin-id"
    await _clean_users(app_instance, prod_project_id, stage_project_id)
    try:
        async with app_instance.state.sessionmaker() as session:
            prod = UserRepository(session, prod_project_id)
            stage = UserRepository(session, stage_project_id)
            await prod.create(
                UserCreate(
                    id="admin",
                    username="prod-admin",
                    online_status="online",
                )
            )
            await stage.create(
                UserCreate(
                    id="admin",
                    username="stage-admin",
                    online_status="offline",
                )
            )
            await session.commit()

            prod_admin = await prod.get("admin")
            stage_admin = await stage.get("admin")
            await prod.set_banned("admin", True)
            stage_admin_after_ban = await stage.get("admin")
            await session.commit()

        assert prod_admin is not None
        assert prod_admin.username == "prod-admin"
        assert prod_admin.online_status == "online"
        assert stage_admin is not None
        assert stage_admin.username == "stage-admin"
        assert stage_admin.online_status == "offline"
        assert stage_admin_after_ban is not None
        assert stage_admin_after_ban.is_banned is False
    finally:
        await _clean_users(app_instance, prod_project_id, stage_project_id)
