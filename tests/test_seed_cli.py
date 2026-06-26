from sqlalchemy import func, select, text

from app.models.alert_event import AlertEvent
from app.seeds.run import run_seed


async def test_run_seed_injects_bulk_data(app_instance):
    # 复用 app_instance 已建好的迁移后的测试库
    async with app_instance.state.sessionmaker() as session:
        await session.execute(
            text(
                "TRUNCATE notifications, audit_logs, heal_actions, "
                "agent_runs, alert_events RESTART IDENTITY"
            )
        )
        await session.commit()

    counts = await run_seed("mochat-prod", reset=True)
    assert counts["alerts"] >= 100
    assert counts["traces"] >= 40

    async with app_instance.state.sessionmaker() as session:
        total = await session.scalar(
            select(func.count()).select_from(AlertEvent).where(
                AlertEvent.project_id == "mochat-prod"
            )
        )
    assert total >= 100
