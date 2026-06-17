import pytest
from sqlalchemy import text

from app.repositories.agent_runs import AgentRunCreate, AgentRunRepository


@pytest.fixture(autouse=True)
async def _clean_agent_runs(app_instance):
    async with app_instance.state.sessionmaker() as session:
        await session.execute(text("DELETE FROM agent_runs"))
        await session.commit()
    yield
    async with app_instance.state.sessionmaker() as session:
        await session.execute(text("DELETE FROM agent_runs"))
        await session.commit()


async def test_agent_run_repository_creates_and_updates_project_scoped_run(app_instance):
    async with app_instance.state.sessionmaker() as session:
        repo = AgentRunRepository(session, "prod")
        run = await repo.create(
            AgentRunCreate(
                trigger_source="manual",
                status="running",
                node_states={},
                evidence_chain={},
                timeline=[],
                rag_results=[],
            )
        )
        await session.commit()

        await repo.finish(
            run.id,
            status="completed",
            conclusion={
                "fault_service": "message-service",
                "anomaly_type": "HighLatency",
                "severity": "warning",
                "confidence": 88.5,
                "root_cause_summary": "DB span became slow",
            },
            node_states={"root_cause_node": {"status": "success"}},
            evidence_chain={"metrics": [], "logs": [], "traces": []},
            timeline=[{"type": "root_cause", "ts": "2026-06-17T00:00:00Z"}],
            rag_results=[],
            stats={
                "llm_calls_count": 1,
                "analyzed_logs_count": 2,
                "related_traces_count": 1,
            },
        )
        await session.commit()

    async with app_instance.state.sessionmaker() as session:
        row = await AgentRunRepository(session, "prod").get(run.id)
        assert row is not None
        assert row.project_id == "prod"
        assert row.status == "completed"
        assert row.fault_service == "message-service"
        assert float(row.confidence) == 88.5
        assert row.node_states["root_cause_node"]["status"] == "success"
        assert await AgentRunRepository(session, "stage").get(run.id) is None


async def test_agent_run_repository_searches_only_same_project(app_instance):
    async with app_instance.state.sessionmaker() as session:
        prod = AgentRunRepository(session, "prod")
        stage = AgentRunRepository(session, "stage")
        await prod.create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="message-service",
                anomaly_type="CrashLoopBackOff",
                severity="critical",
                root_cause_summary="JVM OOM caused pod restart",
                node_states={},
                evidence_chain={},
                timeline=[],
                rag_results=[],
            )
        )
        await stage.create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="message-service",
                anomaly_type="CrashLoopBackOff",
                severity="critical",
                root_cause_summary="stage case must not leak",
                node_states={},
                evidence_chain={},
                timeline=[],
                rag_results=[],
            )
        )
        await session.commit()

        rows = await prod.list_completed(limit=10)

    assert len(rows) == 1
    assert rows[0].project_id == "prod"
    assert rows[0].root_cause_summary == "JVM OOM caused pod restart"
