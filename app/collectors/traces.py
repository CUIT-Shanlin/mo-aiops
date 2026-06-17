from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Protocol

from app.collectors.models import SpanSummary
from app.collectors.windows import TraceCache

TRACE_QUERY_LIMIT = 20


class TempoProvider(Protocol):
    async def query(self, **kwargs: Any) -> Any:
        ...


def _attribute_value(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value:
            return str(value[key])
    return ""


def _service_name(resource: Any) -> str:
    if not isinstance(resource, dict):
        return ""
    attributes = resource.get("attributes")
    if not isinstance(attributes, list):
        return ""
    for attr in attributes:
        if isinstance(attr, dict) and attr.get("key") == "service.name":
            return _attribute_value(attr.get("value"))
    return ""


def _iter_spans(payload: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    if not isinstance(payload, dict):
        return

    batches = payload.get("batches")
    if not isinstance(batches, list):
        return

    for batch in batches:
        if not isinstance(batch, dict):
            continue
        service = _service_name(batch.get("resource"))
        scope_spans_list = batch.get("scopeSpans")
        if not isinstance(scope_spans_list, list):
            continue
        for scope_spans in scope_spans_list:
            if not isinstance(scope_spans, dict):
                continue
            spans = scope_spans.get("spans")
            if not isinstance(spans, list):
                continue
            for span in spans:
                if isinstance(span, dict):
                    yield service, span


def normalize_tempo_trace(trace_id: str, payload: Any) -> list[SpanSummary]:
    """把 Tempo trace payload 转成轻量 span 摘要。"""
    spans: list[SpanSummary] = []
    for service, span in _iter_spans(payload):
        span_id = str(span.get("spanId") or "")
        if not span_id:
            continue

        try:
            duration_ms = float(span.get("durationNanos") or 0) / 1_000_000
        except (TypeError, ValueError):
            duration_ms = 0.0

        status = span.get("status")
        status_code = status.get("code") if isinstance(status, dict) else None
        spans.append(
            SpanSummary(
                traceId=str(span.get("traceId") or trace_id),
                spanId=span_id,
                parentSpanId=str(span.get("parentSpanId") or "") or None,
                service=service,
                name=str(span.get("name") or ""),
                durationMs=duration_ms,
                status="error" if status_code == 2 else "success",
            )
        )
    return spans


async def collect_project_traces(
    *,
    project_id: str,
    trace_ids: Iterable[str],
    provider: TempoProvider,
    trace_cache: TraceCache,
) -> dict[str, list[SpanSummary]]:
    """按日志 traceId 查询 Tempo 并写 trace cache。"""
    results: dict[str, list[SpanSummary]] = {}
    for trace_id in list(dict.fromkeys(trace_ids))[:TRACE_QUERY_LIMIT]:
        try:
            payload = await provider.query(query_type="trace", trace_id=trace_id)
        except Exception:
            results[trace_id] = []
            continue
        spans = normalize_tempo_trace(trace_id, payload)
        results[trace_id] = spans
        if spans:
            trace_cache.put(project_id, trace_id, spans)
    return results
