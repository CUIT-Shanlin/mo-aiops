import json

from app.agent.nodes import (
    AgentNodeContext,
    anomaly_detect_node,
    assemble_evidence_node,
    decide_heal_node,
    fetch_logs_node,
    fetch_metrics_node,
    fetch_traces_node,
    rag_retrieve_node,
    root_cause_node,
)
from app.agent.rag import SimilarCase
from app.agent.state import AgentState
from app.collectors.models import SpanSummary
from app.collectors.windows import MetricWindowStore, TraceCache
from app.core.constants import RedisKey


class FakeRedis:
    def __init__(self, values=None):
        self.values = values or {}

    async def get(self, key):
        return self.values.get(key)


class FakeSimilarCaseService:
    def __init__(self, cases):
        self.cases = cases
        self.queries = []

    async def find_similar_cases(self, query):
        self.queries.append(query)
        return self.cases


async def test_fetch_nodes_build_summaries_and_evidence_from_caches():
    windows = MetricWindowStore(min_samples=1)
    windows.add("prod", "runtime.memory_used_ratio", 0.5)
    windows.add("prod", "runtime.memory_used_ratio", 0.9)

    traces = TraceCache()
    traces.put(
        "prod",
        "trace-1",
        [
            SpanSummary(
                traceId="trace-1",
                spanId="s1",
                service="message-service",
                name="db",
                durationMs=900,
                status="error",
            )
        ],
    )
    redis = FakeRedis(
        {
            RedisKey.of("prod", RedisKey.RECENT_ERRORS): json.dumps(
                [
                    {
                        "timestamp": "2026-06-17T00:00:00Z",
                        "level": "ERROR",
                        "service": "message-service",
                        "message": "OOM",
                        "traceId": "trace-1",
                    }
                ]
            ).encode()
        }
    )
    context = AgentNodeContext(
        redis=redis,
        metric_window_store=windows,
        trace_cache=traces,
    )
    state = AgentState(project_id="prod", trigger_source="manual")

    state = state.model_copy(update=await fetch_metrics_node(state, context))
    state = state.model_copy(update=await fetch_logs_node(state, context))
    state = state.model_copy(update=await fetch_traces_node(state, context))

    assert "runtime.memory_used_ratio" in state.metrics_summary
    assert "ERROR" in state.logs_summary
    assert "trace-1" in state.traces_summary
    assert state.metrics_evidence[0]["metric"] == "runtime.memory_used_ratio"
    assert set(state.metrics_evidence[0]) >= {
        "metric",
        "value",
        "baseline",
        "deviation",
        "severity",
        "timestamp",
        "confidence",
    }
    assert state.logs_evidence[0]["trace_id"] == "trace-1"
    assert set(state.logs_evidence[0]) >= {
        "level",
        "service",
        "message",
        "trace_id",
        "count",
        "first_seen",
        "confidence",
    }
    assert state.traces_evidence[0]["is_slow"] is True
    assert state.traces_evidence[0]["is_error"] is True
    assert set(state.traces_evidence[0]) >= {
        "trace_id",
        "span",
        "service",
        "operation",
        "duration_ms",
        "status",
        "is_slow",
        "is_error",
        "confidence",
    }
    assert set(state.node_states["fetch_metrics_node"]) >= {
        "status",
        "started_at",
        "finished_at",
        "output_summary",
        "tool_calls",
    }
    assert state.node_states["fetch_metrics_node"]["tool_calls"][0]["tool_name"] == "metrics_window"


async def test_fetch_logs_tolerates_missing_and_bad_payloads():
    context = AgentNodeContext(
        redis=FakeRedis({RedisKey.of("prod", RedisKey.RECENT_ERRORS): "{bad json"}),
        metric_window_store=MetricWindowStore(),
        trace_cache=TraceCache(),
    )
    state = AgentState(project_id="prod", trigger_source="manual")

    update = await fetch_logs_node(state, context)

    assert update["logs_summary"] == "no recent errors"
    assert update["logs_evidence"] == []


async def test_anomaly_root_cause_heal_and_evidence_are_deterministic():
    state = AgentState(
        project_id="prod",
        trigger_source="manual",
        metrics_summary="runtime.memory_used_ratio value=0.9 z=4.0 anomaly",
        logs_summary="ERROR OOMKilled",
        traces_summary="trace error",
        metrics_evidence=[
            {
                "metric": "runtime.memory_used_ratio",
                "is_anomaly": True,
                "value": 0.9,
            }
        ],
        logs_evidence=[
            {
                "level": "ERROR",
                "service": "message-service",
                "message": "OutOfMemoryError",
            }
        ],
        traces_evidence=[
            {
                "trace_id": "t1",
                "service": "message-service",
                "duration_ms": 900,
                "is_error": True,
                "is_slow": True,
            }
        ],
    )
    context = AgentNodeContext(
        redis=FakeRedis(),
        metric_window_store=MetricWindowStore(),
        trace_cache=TraceCache(),
    )

    state = state.model_copy(update=await anomaly_detect_node(state, context))
    state = state.model_copy(update=await root_cause_node(state, context))
    state = state.model_copy(update=await decide_heal_node(state, context))
    state = state.model_copy(update=await assemble_evidence_node(state, context))

    assert state.anomaly_detected is True
    assert state.fault_service == "message-service"
    assert state.anomaly_type in {"OOMKilled", "HighMemory"}
    assert state.action_type in {"pod_restart", "hpa_scale"}
    assert state.evidence_chain == {
        "metrics": state.metrics_evidence,
        "logs": state.logs_evidence,
        "traces": state.traces_evidence,
    }
    assert any(item["type"] == "root_cause" for item in state.timeline)
    assert state.stats["analyzed_logs_count"] == 1
    assert state.node_states["assemble_evidence_node"]["status"] == "success"
    timeline_types = {item["type"] for item in state.timeline}
    assert {
        "agent_started",
        "metric_deviation",
        "log_anomaly",
        "trace_anomaly",
        "root_cause",
        "heal_decision",
    } <= timeline_types
    assert all({"ts", "type", "title", "detail"} <= set(item) for item in state.timeline)


async def test_rag_retrieve_infers_query_before_root_cause_node():
    fake_service = FakeSimilarCaseService(
        [
            SimilarCase(
                caseId="case-1",
                title="message-service OOMKilled",
                similarity=0.91,
                rootCause="historical OOM",
                resolution="restart pod",
                resolvedAt="2026-06-17T00:00:00Z",
            )
        ]
    )
    state = AgentState(
        project_id="prod",
        trigger_source="manual",
        logs_evidence=[
            {
                "level": "ERROR",
                "service": "message-service",
                "message": "OutOfMemoryError",
            }
        ],
    )
    context = AgentNodeContext(
        redis=FakeRedis(),
        metric_window_store=MetricWindowStore(),
        trace_cache=TraceCache(),
        session=object(),
        similar_case_service=fake_service,
    )

    update = await rag_retrieve_node(state, context)

    assert update["rag_results"][0]["caseId"] == "case-1"
    assert fake_service.queries[0].alert_type == "OOMKilled"
    assert fake_service.queries[0].service == "message-service"


class FakeAlert:
    def __init__(self, *, id, name, service, severity, status):
        self.id = id
        self.name = name
        self.service = service
        self.severity = severity
        self.status = status


async def test_anomaly_detect_treats_linked_active_alert_as_anomaly():
    async def loader(alert_id):
        return FakeAlert(
            id=alert_id,
            name="HighCPU",
            service="message-service",
            severity="critical",
            status="firing",
        )

    state = AgentState(
        project_id="prod",
        trigger_source="alert",
        alert_event_id=42,
    )
    context = AgentNodeContext(
        redis=FakeRedis(),
        metric_window_store=MetricWindowStore(),
        trace_cache=TraceCache(),
        alert_loader=loader,
    )

    update = await anomaly_detect_node(state, context)

    assert update["anomaly_detected"] is True
    assert update["severity"] == "critical"
    assert update["fault_service"] == "message-service"
    assert update["anomaly_type"] == "HighCPU"


async def test_anomaly_detect_ignores_resolved_linked_alert():
    async def loader(alert_id):
        return FakeAlert(
            id=alert_id,
            name="HighCPU",
            service="message-service",
            severity="warning",
            status="resolved",
        )

    state = AgentState(
        project_id="prod",
        trigger_source="alert",
        alert_event_id=42,
    )
    context = AgentNodeContext(
        redis=FakeRedis(),
        metric_window_store=MetricWindowStore(),
        trace_cache=TraceCache(),
        alert_loader=loader,
    )

    update = await anomaly_detect_node(state, context)

    assert update["anomaly_detected"] is False


async def test_anomaly_detect_without_alert_loader_falls_back_to_evidence():
    state = AgentState(
        project_id="prod",
        trigger_source="manual",
        metrics_evidence=[{"is_anomaly": True, "metric": "sys.cpu", "value": 99}],
    )
    context = AgentNodeContext(
        redis=FakeRedis(),
        metric_window_store=MetricWindowStore(),
        trace_cache=TraceCache(),
    )

    update = await anomaly_detect_node(state, context)

    assert update["anomaly_detected"] is True
