from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import text

from app.agent.rag import SimilarCaseQuery, SimilarCaseService, score_case
from app.repositories.agent_runs import AgentRunCreate, AgentRunRepository


async def _clear_agent_runs(app_instance) -> None:
    async with app_instance.state.sessionmaker() as session:
        await session.execute(text("DELETE FROM agent_runs"))
        await session.commit()


def test_score_case_prefers_same_service_anomaly_and_token_overlap():
    exact = score_case(
        alert_type="CrashLoopBackOff",
        service="message-service",
        run={
            "fault_service": "message-service",
            "anomaly_type": "CrashLoopBackOff",
            "root_cause_summary": "message service JVM OOM caused repeated pod restarts",
            "severity": "critical",
        },
    )
    service_only = score_case(
        alert_type="CrashLoopBackOff",
        service="message-service",
        run={
            "fault_service": "message-service",
            "anomaly_type": "HighLatency",
            "root_cause_summary": "database slow query increased p99 latency",
            "severity": "warning",
        },
    )
    weak = score_case(
        alert_type="CrashLoopBackOff",
        service="message-service",
        run={
            "fault_service": "user-service",
            "anomaly_type": "HighLatency",
            "root_cause_summary": "database slow query",
            "severity": "warning",
        },
    )

    assert exact > service_only > weak
    assert 0.0 <= weak <= 1.0
    assert 0.0 <= service_only <= 1.0
    assert 0.0 <= exact <= 1.0


async def test_similar_case_service_returns_ranked_api_shape(app_instance):
    await _clear_agent_runs(app_instance)
    async with app_instance.state.sessionmaker() as session:
        repo = AgentRunRepository(session, "prod")
        older_match = await repo.create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="message-service",
                anomaly_type="CrashLoopBackOff",
                severity="critical",
                confidence=90,
                root_cause_summary="JVM Heap OOM caused pod restart",
                node_states={},
                evidence_chain={},
                timeline=[{"ts": "2026-03-12T14:22:00Z", "type": "resolved"}],
                rag_results=[],
            )
        )
        await session.flush()
        newer_weaker = await repo.create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="message-service",
                anomaly_type="HighLatency",
                severity="warning",
                confidence=70,
                root_cause_summary="database slow query increased message latency",
                node_states={},
                evidence_chain={},
                timeline=[],
                rag_results=[],
            )
        )
        await session.commit()

        cases = await SimilarCaseService(session, "prod").find_similar_cases(
            SimilarCaseQuery(alert_type="CrashLoopBackOff", service="message-service")
        )

    assert [case.caseId for case in cases] == [f"case-{older_match.id}", f"case-{newer_weaker.id}"]
    assert cases[0].title == "message-service CrashLoopBackOff"
    assert cases[0].rootCause == "JVM Heap OOM caused pod restart"
    assert cases[0].resolution == "参考历史 RCA 结论与处置记录"
    assert cases[0].resolvedAt is not None
    assert cases[0].similarity >= cases[1].similarity
    assert cases[0].model_dump().keys() == {
        "caseId",
        "title",
        "similarity",
        "rootCause",
        "resolution",
        "resolvedAt",
    }


async def test_similar_case_service_filters_project_and_completed_runs(app_instance):
    await _clear_agent_runs(app_instance)
    async with app_instance.state.sessionmaker() as session:
        prod = AgentRunRepository(session, "prod")
        stage = AgentRunRepository(session, "stage")
        prod_completed = await prod.create(
            AgentRunCreate(
                trigger_source="manual",
                status="completed",
                fault_service="message-service",
                anomaly_type="CrashLoopBackOff",
                severity="critical",
                root_cause_summary="prod case should be visible",
                node_states={},
                evidence_chain={},
                timeline=[],
                rag_results=[],
            )
        )
        await prod.create(
            AgentRunCreate(
                trigger_source="manual",
                status="running",
                fault_service="message-service",
                anomaly_type="CrashLoopBackOff",
                severity="critical",
                root_cause_summary="running prod case must not leak",
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

        cases = await SimilarCaseService(session, "prod").find_similar_cases(
            SimilarCaseQuery(alert_type="CrashLoopBackOff", service="message-service", limit=5)
        )

    assert [case.caseId for case in cases] == [f"case-{prod_completed.id}"]
    assert cases[0].rootCause == "prod case should be visible"


async def test_similar_case_service_uses_run_id_as_final_tiebreaker():
    same_time = datetime(2026, 6, 17, tzinfo=UTC)

    class FakeRepo:
        async def list_completed(self, *, limit: int):
            return [
                SimpleNamespace(
                    id=1,
                    fault_service="message-service",
                    anomaly_type="CrashLoopBackOff",
                    severity="critical",
                    root_cause_summary="same score older id",
                    finished_at=same_time,
                    created_at=same_time,
                ),
                SimpleNamespace(
                    id=2,
                    fault_service="message-service",
                    anomaly_type="CrashLoopBackOff",
                    severity="critical",
                    root_cause_summary="same score newer id",
                    finished_at=same_time,
                    created_at=same_time,
                ),
            ]

    service = SimilarCaseService.__new__(SimilarCaseService)
    service.repo = FakeRepo()

    cases = await service.find_similar_cases(
        SimilarCaseQuery(alert_type="CrashLoopBackOff", service="message-service")
    )

    assert [case.caseId for case in cases] == ["case-2", "case-1"]
