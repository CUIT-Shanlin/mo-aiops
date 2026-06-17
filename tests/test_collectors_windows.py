import math

from app.collectors.models import MetricSample, SpanSummary
from app.collectors.windows import MetricWindowStore, TraceCache


def test_metric_window_returns_no_zscore_until_min_samples():
    store = MetricWindowStore(maxlen=40, min_samples=10, zscore_threshold=3.0)

    sample = store.add(project_id="demo", canonical_name="conn.active", value=10.0)

    assert sample == MetricSample(
        canonicalName="conn.active",
        value=10.0,
        zScore=None,
        isAnomaly=False,
    )
    assert store.snapshot("demo") == [sample]


def test_metric_window_flags_anomaly_after_baseline():
    store = MetricWindowStore(maxlen=40, min_samples=10, zscore_threshold=2.0)
    for _ in range(10):
        store.add("demo", "conn.active", 10.0)

    sample = store.add("demo", "conn.active", 30.0)

    assert sample.canonicalName == "conn.active"
    assert sample.value == 30.0
    assert sample.zScore is not None
    assert sample.zScore > 2.0
    assert sample.isAnomaly is True


def test_metric_window_scores_new_value_against_history_before_appending():
    store = MetricWindowStore(maxlen=40, min_samples=3, zscore_threshold=2.0)
    store.add("demo", "conn.active", 10.0)
    store.add("demo", "conn.active", 10.0)
    store.add("demo", "conn.active", 10.0)

    sample = store.add("demo", "conn.active", 30.0)

    assert sample.isAnomaly is True
    assert sample.zScore is not None
    assert math.isfinite(sample.zScore)
    assert abs(sample.zScore) > 2.0


def test_metric_window_is_project_scoped():
    store = MetricWindowStore(maxlen=40, min_samples=1, zscore_threshold=3.0)

    store.add("prod", "conn.active", 10.0)
    store.add("stage", "conn.active", 20.0)

    assert [sample.value for sample in store.snapshot("prod")] == [10.0]
    assert [sample.value for sample in store.snapshot("stage")] == [20.0]


def test_metric_window_is_canonical_name_scoped_within_project():
    store = MetricWindowStore(maxlen=40, min_samples=2, zscore_threshold=2.0)

    store.add("prod", "conn.active", 10.0)
    store.add("prod", "conn.active", 10.0)
    latency_sample = store.add("prod", "msg.p99_latency", 99.0)
    conn_sample = store.add("prod", "conn.active", 30.0)

    assert latency_sample.zScore is None
    assert conn_sample.zScore is not None
    assert [sample.canonicalName for sample in store.snapshot("prod")] == [
        "conn.active",
        "msg.p99_latency",
    ]


def test_metric_window_zero_std_sets_zero_zscore_without_anomaly():
    store = MetricWindowStore(maxlen=40, min_samples=3, zscore_threshold=2.0)

    store.add("prod", "conn.active", 10.0)
    store.add("prod", "conn.active", 10.0)
    store.add("prod", "conn.active", 10.0)
    sample = store.add("prod", "conn.active", 10.0)

    assert sample.zScore == 0.0
    assert sample.isAnomaly is False


def test_trace_cache_is_project_scoped_and_evicts_oldest():
    cache = TraceCache(max_traces_per_project=2)
    span = SpanSummary(
        traceId="t1",
        spanId="s1",
        parentSpanId=None,
        service="api",
        name="GET /health",
        durationMs=12.0,
        status="success",
    )

    cache.put("prod", "t1", [span])
    cache.put("prod", "t2", [])
    cache.put("prod", "t3", [])
    cache.put("stage", "t1", [span])

    assert cache.get("prod", "t1") is None
    assert list(cache.snapshot("prod")) == [("t2", []), ("t3", [])]
    assert list(cache.snapshot("stage")) == [("t1", [span])]


def test_trace_cache_update_existing_trace_keeps_fifo_order():
    cache = TraceCache(max_traces_per_project=2)
    updated_span = SpanSummary(
        traceId="t1",
        spanId="s2",
        parentSpanId=None,
        service="api",
        name="GET /ready",
        durationMs=8.0,
        status="success",
    )

    cache.put("prod", "t1", [])
    cache.put("prod", "t2", [])
    cache.put("prod", "t1", [updated_span])
    cache.put("prod", "t3", [])

    assert cache.get("prod", "t1") is None
    assert list(cache.snapshot("prod")) == [("t2", []), ("t3", [])]
