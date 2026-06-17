from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from statistics import mean, pstdev
from typing import Deque, Iterable

from app.collectors.models import MetricSample, SpanSummary


class MetricWindowStore:
    """按 project + canonical 指标维护内存滑动窗口。"""

    def __init__(
        self,
        *,
        maxlen: int = 40,
        min_samples: int = 10,
        zscore_threshold: float = 3.0,
    ) -> None:
        self.maxlen = maxlen
        self.min_samples = min_samples
        self.zscore_threshold = zscore_threshold
        self._windows: dict[tuple[str, str], Deque[float]] = defaultdict(
            lambda: deque(maxlen=maxlen)
        )
        self._latest: dict[str, dict[str, MetricSample]] = defaultdict(dict)

    def add(
        self,
        project_id: str,
        canonical_name: str,
        value: float,
    ) -> MetricSample:
        window = self._windows[(project_id, canonical_name)]
        z_score: float | None = None
        is_anomaly = False
        if len(window) >= self.min_samples:
            baseline = list(window)
            baseline_mean = mean(baseline)
            std = pstdev(baseline)
            if std == 0:
                if value == baseline_mean:
                    z_score = 0.0
                else:
                    capped_z_score = self.zscore_threshold + 1.0
                    z_score = capped_z_score if value > baseline_mean else -capped_z_score
            else:
                z_score = (value - baseline_mean) / std
            is_anomaly = abs(z_score) > self.zscore_threshold

        window.append(value)
        sample = MetricSample(
            canonicalName=canonical_name,
            value=value,
            zScore=z_score,
            isAnomaly=is_anomaly,
        )
        self._latest[project_id][canonical_name] = sample
        return sample

    def snapshot(self, project_id: str) -> list[MetricSample]:
        return list(self._latest.get(project_id, {}).values())


class TraceCache:
    """按 project 存储最新 trace span 树，超量 FIFO 淘汰。"""

    def __init__(self, *, max_traces_per_project: int = 200) -> None:
        self.max_traces_per_project = max_traces_per_project
        self._traces: dict[str, OrderedDict[str, list[SpanSummary]]] = defaultdict(
            OrderedDict
        )

    def put(
        self,
        project_id: str,
        trace_id: str,
        spans: list[SpanSummary],
    ) -> None:
        traces = self._traces[project_id]
        traces[trace_id] = spans
        while len(traces) > self.max_traces_per_project:
            traces.popitem(last=False)

    def get(self, project_id: str, trace_id: str) -> list[SpanSummary] | None:
        traces = self._traces.get(project_id)
        if traces is None:
            return None
        return traces.get(trace_id)

    def snapshot(self, project_id: str) -> Iterable[tuple[str, list[SpanSummary]]]:
        traces = self._traces.get(project_id)
        if traces is None:
            return []
        return list(traces.items())
