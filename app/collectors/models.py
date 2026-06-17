from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel


class MetricSample(BaseModel):
    canonicalName: str
    value: float
    zScore: float | None = None
    isAnomaly: bool = False


class LogEntry(BaseModel):
    timestamp: str
    level: str
    service: str = ""
    namespace: str = ""
    pod: str = ""
    traceId: str = ""
    spanId: str = ""
    message: str = ""


class SpanSummary(BaseModel):
    traceId: str
    spanId: str
    parentSpanId: str | None = None
    service: str = ""
    name: str = ""
    durationMs: float = 0.0
    status: Literal["success", "error"] = "success"


class MetricsSnapshot(BaseModel):
    type: str = "metrics.snapshot"
    projectId: str
    timestamp: str
    metrics: list[MetricSample]


@dataclass(slots=True)
class ProjectCollectionResult:
    project_id: str
    metrics: list[MetricSample] = field(default_factory=list)
    logs: list[LogEntry] = field(default_factory=list)
    traces: dict[str, list[SpanSummary]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
