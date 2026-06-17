from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.agent.rag import SimilarCaseQuery, SimilarCaseService
from app.agent.state import AgentState
from app.collectors.windows import MetricWindowStore, TraceCache
from app.core.constants import RedisKey


@dataclass(slots=True)
class AgentNodeContext:
    redis: Any
    metric_window_store: MetricWindowStore
    trace_cache: TraceCache
    session: Any | None = None
    llm_client: Any | None = None
    similar_case_service: Any | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _tool_call(
    *,
    tool_name: str,
    input_summary: str,
    result_summary: str,
    success: bool = True,
    duration_ms: float = 0.0,
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "input_summary": input_summary,
        "duration_ms": duration_ms,
        "result_summary": result_summary,
        "success": success,
    }


def _node_state(
    *,
    output_summary: str,
    tool_calls: list[dict[str, Any]] | None = None,
    status: str = "success",
    **extra: Any,
) -> dict[str, Any]:
    ts = _now_iso()
    return {
        "status": status,
        "started_at": ts,
        "finished_at": ts,
        "output_summary": output_summary,
        "tool_calls": tool_calls or [],
        **extra,
    }


def _merge_node_state(
    state: AgentState,
    node_name: str,
    node_state: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {**state.node_states, node_name: node_state}


def _merge_summaries(
    state: AgentState,
    key: str,
    value: str,
) -> dict[str, Any]:
    return {**state.summaries, key: value}


async def fetch_metrics_node(
    state: AgentState,
    context: AgentNodeContext,
) -> dict[str, Any]:
    samples = context.metric_window_store.snapshot(state.project_id)
    evidence = [
        {
            "metric": sample.canonicalName,
            "value": sample.value,
            "baseline": None,
            "deviation": sample.zScore,
            "z_score": sample.zScore,
            "severity": "warning" if sample.isAnomaly else "info",
            "timestamp": _now_iso(),
            "confidence": 0.8 if sample.isAnomaly else 0.3,
            "is_anomaly": sample.isAnomaly,
        }
        for sample in samples
    ]
    summary = "; ".join(
        _format_metric(item)
        for item in evidence
    ) or "no metrics"

    return {
        "metrics_summary": summary,
        "metrics_evidence": evidence,
        "summaries": _merge_summaries(state, "metrics", summary),
        "node_states": _merge_node_state(
            state,
            "fetch_metrics_node",
            _node_state(
                output_summary=summary,
                tool_calls=[
                    _tool_call(
                        tool_name="metrics_window",
                        input_summary=f"project_id={state.project_id}",
                        result_summary=f"{len(evidence)} metrics",
                    )
                ],
                metrics_count=len(evidence),
            ),
        ),
    }


def _format_metric(item: dict[str, Any]) -> str:
    z_score = item["z_score"]
    z_part = "z=None" if z_score is None else f"z={z_score:.2f}"
    anomaly_part = " anomaly" if item["is_anomaly"] else ""
    return f"{item['metric']} value={item['value']} {z_part}{anomaly_part}"


async def fetch_logs_node(
    state: AgentState,
    context: AgentNodeContext,
) -> dict[str, Any]:
    raw = await context.redis.get(RedisKey.of(state.project_id, RedisKey.RECENT_ERRORS))
    rows = _parse_log_payload(raw)
    evidence = [
        {
            "timestamp": str(row.get("timestamp") or ""),
            "level": str(row.get("level") or "").upper(),
            "service": str(row.get("service") or ""),
            "namespace": str(row.get("namespace") or ""),
            "pod": str(row.get("pod") or ""),
            "trace_id": str(row.get("traceId") or row.get("trace_id") or ""),
            "span_id": str(row.get("spanId") or row.get("span_id") or ""),
            "message": str(row.get("message") or ""),
            "count": 1,
            "first_seen": str(row.get("timestamp") or ""),
            "confidence": 0.8,
        }
        for row in rows
        if isinstance(row, dict)
    ]
    summary = "; ".join(
        _format_log(item)
        for item in evidence
    ) or "no recent errors"

    return {
        "logs_summary": summary,
        "logs_evidence": evidence,
        "summaries": _merge_summaries(state, "logs", summary),
        "node_states": _merge_node_state(
            state,
            "fetch_logs_node",
            _node_state(
                output_summary=summary,
                tool_calls=[
                    _tool_call(
                        tool_name="redis",
                        input_summary=RedisKey.of(state.project_id, RedisKey.RECENT_ERRORS),
                        result_summary=f"{len(evidence)} logs",
                    )
                ],
                logs_count=len(evidence),
            ),
        ),
    }


def _parse_log_payload(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
    else:
        payload = raw
    return payload if isinstance(payload, list) else []


def _format_log(item: dict[str, Any]) -> str:
    service = item["service"] or "unknown-service"
    message = item["message"]
    return f"{item['level']} {service} {message}".strip()


async def fetch_traces_node(
    state: AgentState,
    context: AgentNodeContext,
) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    for trace_id, spans in context.trace_cache.snapshot(state.project_id):
        for span in spans:
            is_slow = span.durationMs > 500
            is_error = span.status == "error"
            if not is_slow and not is_error:
                continue
            evidence.append(
                {
                    "trace_id": trace_id,
                    "span_id": span.spanId,
                    "span": span.spanId,
                    "service": span.service,
                    "operation": span.name,
                    "name": span.name,
                    "duration_ms": span.durationMs,
                    "status": span.status,
                    "is_slow": is_slow,
                    "is_error": is_error,
                    "confidence": 0.85 if is_error else 0.65,
                }
            )
    summary = "; ".join(
        _format_trace(item)
        for item in evidence
    ) or "no trace anomalies"

    return {
        "traces_summary": summary,
        "traces_evidence": evidence,
        "summaries": _merge_summaries(state, "traces", summary),
        "node_states": _merge_node_state(
            state,
            "fetch_traces_node",
            _node_state(
                output_summary=summary,
                tool_calls=[
                    _tool_call(
                        tool_name="trace_cache",
                        input_summary=f"project_id={state.project_id}",
                        result_summary=f"{len(evidence)} anomalous spans",
                    )
                ],
                traces_count=len(evidence),
            ),
        ),
    }


def _format_trace(item: dict[str, Any]) -> str:
    flags = []
    if item["is_error"]:
        flags.append("error")
    if item["is_slow"]:
        flags.append("slow")
    return (
        f"{item['trace_id']} {item['service'] or 'unknown-service'} "
        f"{item['name']} {item['duration_ms']}ms {'/'.join(flags)}"
    ).strip()


async def anomaly_detect_node(
    state: AgentState,
    context: AgentNodeContext,
) -> dict[str, Any]:
    del context
    anomaly_detected = (
        any(item.get("is_anomaly") for item in state.metrics_evidence)
        or any((item.get("level") or "").upper() == "ERROR" for item in state.logs_evidence)
        or any(
            item.get("is_error") or item.get("is_slow")
            for item in state.traces_evidence
        )
    )
    severity = _determine_severity(state, anomaly_detected)
    confidence = 0.0 if not anomaly_detected else min(
        95.0,
        55.0
        + 15.0 * bool(state.metrics_evidence)
        + 15.0 * bool(state.logs_evidence)
        + 10.0 * bool(state.traces_evidence),
    )

    return {
        "anomaly_detected": anomaly_detected,
        "severity": severity,
        "confidence": confidence,
        "node_states": _merge_node_state(
            state,
            "anomaly_detect_node",
            _node_state(
                output_summary=(
                    "anomaly detected" if anomaly_detected else "no anomaly detected"
                ),
                tool_calls=[],
                anomaly_detected=anomaly_detected,
            ),
        ),
    }


def _determine_severity(
    state: AgentState,
    anomaly_detected: bool,
) -> str:
    if not anomaly_detected:
        return "info"
    if any(
        (item.get("level") or "").upper() == "ERROR"
        for item in state.logs_evidence
    ) or any(item.get("is_error") for item in state.traces_evidence):
        return "critical"
    return "warning"


async def rag_retrieve_node(
    state: AgentState,
    context: AgentNodeContext,
) -> dict[str, Any]:
    anomaly_type = state.anomaly_type or _pick_anomaly_type(state)
    service = state.fault_service or _pick_fault_service(state)
    cases: list[dict[str, Any]]
    if context.similar_case_service is not None:
        result = await context.similar_case_service.find_similar_cases(
            SimilarCaseQuery(
                alert_type=anomaly_type,
                service=service,
            )
        )
        cases = [case.model_dump() for case in result]
    elif context.session is None or anomaly_type == "UnknownAnomaly":
        cases = []
    else:
        result = await SimilarCaseService(
            context.session,
            state.project_id,
        ).find_similar_cases(
            SimilarCaseQuery(
                alert_type=anomaly_type,
                service=service,
            )
        )
        cases = [case.model_dump() for case in result]

    return {
        "rag_results": cases,
        "node_states": _merge_node_state(
            state,
            "rag_retrieve_node",
            _node_state(
                output_summary=f"retrieved {len(cases)} similar cases",
                tool_calls=[
                    _tool_call(
                        tool_name="rag",
                        input_summary=f"service={service}, alert_type={anomaly_type}",
                        result_summary=f"{len(cases)} cases",
                    )
                ],
                rag_results_count=len(cases),
            ),
        ),
    }


async def root_cause_node(
    state: AgentState,
    context: AgentNodeContext,
) -> dict[str, Any]:
    del context
    fault_service = _pick_fault_service(state)
    anomaly_type = _pick_anomaly_type(state)
    summary = _root_cause_summary(
        service=fault_service,
        anomaly_type=anomaly_type,
        state=state,
    )
    conclusion = {
        "fault_service": fault_service,
        "anomaly_type": anomaly_type,
        "severity": state.severity or "warning",
        "confidence": state.confidence or 0.0,
        "root_cause_summary": summary,
    }

    return {
        "fault_service": fault_service,
        "anomaly_type": anomaly_type,
        "root_cause_summary": summary,
        "conclusion": conclusion,
        "node_states": _merge_node_state(
            state,
            "root_cause_node",
            _node_state(
                output_summary=summary,
                tool_calls=[],
                fault_service=fault_service,
                anomaly_type=anomaly_type,
            ),
        ),
    }


def _pick_fault_service(state: AgentState) -> str:
    for item in state.logs_evidence:
        service = item.get("service")
        if service:
            return str(service)
    for item in state.traces_evidence:
        service = item.get("service")
        if service:
            return str(service)
    return "unknown-service"


def _pick_anomaly_type(state: AgentState) -> str:
    log_text = " ".join(
        str(item.get("message") or "")
        for item in state.logs_evidence
    ).lower()
    if "outofmemory" in log_text or "oom" in log_text or "oomkilled" in log_text:
        return "OOMKilled"
    if any(
        item.get("metric") == "runtime.memory_used_ratio" and item.get("is_anomaly")
        for item in state.metrics_evidence
    ):
        return "HighMemory"
    if any(item.get("is_error") for item in state.traces_evidence) or any(
        (item.get("level") or "").upper() == "ERROR"
        for item in state.logs_evidence
    ):
        return "HighErrorRate"
    if any(item.get("is_slow") for item in state.traces_evidence):
        return "HighLatency"
    if any(item.get("is_anomaly") for item in state.metrics_evidence):
        metric = str(state.metrics_evidence[0].get("metric") or "")
        if "cpu" in metric:
            return "HighCPU"
        if "mq" in metric or "backlog" in metric:
            return "HighMQBacklog"
    return "UnknownAnomaly"


def _root_cause_summary(
    *,
    service: str,
    anomaly_type: str,
    state: AgentState,
) -> str:
    signals = []
    if state.metrics_evidence:
        signals.append("metrics")
    if state.logs_evidence:
        signals.append("logs")
    if state.traces_evidence:
        signals.append("traces")
    signal_text = ", ".join(signals) or "available signals"
    return f"{service} shows {anomaly_type} based on {signal_text}"


async def decide_heal_node(
    state: AgentState,
    context: AgentNodeContext,
) -> dict[str, Any]:
    del context
    anomaly_type = state.anomaly_type or "UnknownAnomaly"
    action_type = _pick_action_type(anomaly_type)
    risk_level = _risk_level(action_type)
    auto_heal = action_type in {"pod_restart", "hpa_scale"}
    target_resource = state.fault_service or "unknown-service"
    decision = {
        "action_type": action_type,
        "target_resource": target_resource,
        "auto_heal": auto_heal,
        "risk_level": risk_level,
        "reason": f"{anomaly_type} maps to {action_type}",
    }

    return {
        "action_type": action_type,
        "target_resource": target_resource,
        "auto_heal": auto_heal,
        "risk_level": risk_level,
        "healing_decision": decision,
        "node_states": _merge_node_state(
            state,
            "decide_heal_node",
            _node_state(
                output_summary=f"{action_type} risk={risk_level}",
                tool_calls=[],
                action_type=action_type,
                risk_level=risk_level,
            ),
        ),
    }


def _pick_action_type(anomaly_type: str) -> str:
    if anomaly_type in {"OOMKilled", "CrashLoopBackOff"}:
        return "pod_restart"
    if anomaly_type in {"HighCPU", "HighMemory", "HighMQBacklog"}:
        return "hpa_scale"
    if anomaly_type == "NodeNotReady":
        return "node_evict"
    return "none"


def _risk_level(action_type: str) -> str:
    if action_type == "node_evict":
        return "high"
    if action_type in {"pod_restart", "hpa_scale"}:
        return "medium"
    return "none"


async def assemble_evidence_node(
    state: AgentState,
    context: AgentNodeContext,
) -> dict[str, Any]:
    del context
    evidence_chain = {
        "metrics": state.metrics_evidence,
        "logs": state.logs_evidence,
        "traces": state.traces_evidence,
    }
    timeline = [*state.timeline, *_build_timeline_events(state)]
    stats = {
        **state.stats,
        "llm_calls_count": state.stats.get("llm_calls_count", 0),
        "analyzed_logs_count": len(state.logs_evidence),
        "related_traces_count": len(state.traces_evidence),
        "metrics_count": len(state.metrics_evidence),
    }

    return {
        "evidence_chain": evidence_chain,
        "timeline": timeline,
        "stats": stats,
        "node_states": _merge_node_state(
            state,
            "assemble_evidence_node",
            _node_state(
                output_summary=(
                    f"assembled {len(state.metrics_evidence)} metrics, "
                    f"{len(state.logs_evidence)} logs, "
                    f"{len(state.traces_evidence)} traces"
                ),
                tool_calls=[],
            ),
        ),
    }


def _build_timeline_events(state: AgentState) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "type": "agent_started",
            "ts": _now_iso(),
            "title": "Agent 开始分析",
            "detail": f"trigger_source={state.trigger_source}",
            "trigger_source": state.trigger_source,
        }
    ]
    events.extend(
        {
            "type": "metric_deviation",
            "ts": _now_iso(),
            "title": "指标偏离动态基线",
            "metric": item.get("metric"),
            "detail": f"{item.get('metric')} value={item.get('value')} z={item.get('z_score')}",
        }
        for item in state.metrics_evidence
        if item.get("is_anomaly")
    )
    events.extend(
        {
            "type": "log_anomaly",
            "ts": item.get("timestamp") or _now_iso(),
            "title": "日志异常增加",
            "service": item.get("service"),
            "detail": item.get("message"),
        }
        for item in state.logs_evidence
    )
    events.extend(
        {
            "type": "trace_anomaly",
            "ts": _now_iso(),
            "title": "Trace 发现慢或错误调用",
            "trace_id": item.get("trace_id"),
            "service": item.get("service"),
            "detail": f"{item.get('name', 'span')} {item.get('duration_ms')}ms",
        }
        for item in state.traces_evidence
    )
    events.append(
        {
            "type": "rag_retrieval",
            "ts": _now_iso(),
            "title": "RAG 召回历史案例",
            "detail": f"retrieved {len(state.rag_results)} similar cases",
            "count": len(state.rag_results),
        }
    )
    events.append(
        {
            "type": "root_cause",
            "ts": _now_iso(),
            "title": "给出根因结论",
            "service": state.fault_service,
            "anomaly_type": state.anomaly_type,
            "summary": state.root_cause_summary,
            "detail": state.root_cause_summary or "",
        }
    )
    events.append(
        {
            "type": "heal_decision",
            "ts": _now_iso(),
            "title": "生成自愈决策",
            "action_type": state.action_type,
            "risk_level": state.risk_level,
            "detail": f"action={state.action_type}, risk={state.risk_level}",
        }
    )
    return events
