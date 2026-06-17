import pytest

from app.collectors.metrics import collect_project_metrics, extract_prometheus_value
from app.collectors.windows import MetricWindowStore
from app.core.projects import ProjectConfig


class FakePrometheusProvider:
    def __init__(self):
        self.queries = []

    async def query(self, **kwargs):
        self.queries.append(kwargs)
        return {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [1781623482.0, "42.5"]}],
            },
        }


class FailingThenSuccessfulPrometheusProvider:
    def __init__(self):
        self.queries = []

    async def query(self, **kwargs):
        self.queries.append(kwargs)
        if len(self.queries) == 1:
            raise RuntimeError("prometheus unavailable")
        return {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [1781623482.0, "7.5"]}],
            },
        }


class BadPayloadThenSuccessfulPrometheusProvider:
    def __init__(self):
        self.queries = []

    async def query(self, **kwargs):
        self.queries.append(kwargs)
        if len(self.queries) == 1:
            return []
        return {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [1781623482.0, "8.5"]}],
            },
        }


def test_extract_prometheus_value_reads_vector_sample_string():
    payload = {
        "data": {
            "result": [
                {"metric": {"service": "api"}, "value": [1781623482.0, "3.14"]}
            ]
        }
    }

    assert extract_prometheus_value(payload) == 3.14


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "bad",
        {},
        {"data": {"result": []}},
        {"data": {"result": [{"value": [1, "NaN"]}]}},
        {"data": {"result": [{"value": [1, "+Inf"]}]}},
        {"data": {"result": [{"value": [1, "-Inf"]}]}},
        {"data": None},
        {"data": {"result": [None]}},
        {"data": {"result": [{"values": []}]}},
        {"data": {"result": "bad"}},
    ],
)
def test_extract_prometheus_value_returns_none_for_unusable_samples(payload):
    assert extract_prometheus_value(payload) is None


async def test_collect_project_metrics_resolves_canonical_promql():
    provider = FakePrometheusProvider()
    store = MetricWindowStore(min_samples=10)
    project = ProjectConfig(name="Demo", metric_profile="java")

    samples = await collect_project_metrics(
        project_id="demo",
        project=project,
        provider=provider,
        window_store=store,
        canonical_metrics=["conn.active", "runtime.memory_used_ratio"],
    )

    assert [sample.canonicalName for sample in samples] == [
        "conn.active",
        "runtime.memory_used_ratio",
    ]
    assert [sample.value for sample in samples] == [42.5, 42.5]
    assert provider.queries == [
        {"query_type": "instant", "query": "sum(netty_connections_active_total)"},
        {
            "query_type": "instant",
            "query": "sum(jvm_memory_used_bytes) / sum(jvm_memory_max_bytes)",
        },
    ]


async def test_collect_project_metrics_skips_bad_payload_and_continues():
    provider = BadPayloadThenSuccessfulPrometheusProvider()
    store = MetricWindowStore(min_samples=10)
    project = ProjectConfig(name="Demo", metric_profile="java")

    samples = await collect_project_metrics(
        project_id="demo",
        project=project,
        provider=provider,
        window_store=store,
        canonical_metrics=["conn.active", "runtime.memory_used_ratio"],
    )

    assert [sample.canonicalName for sample in samples] == [
        "runtime.memory_used_ratio"
    ]
    assert [sample.value for sample in samples] == [8.5]
    assert provider.queries == [
        {"query_type": "instant", "query": "sum(netty_connections_active_total)"},
        {
            "query_type": "instant",
            "query": "sum(jvm_memory_used_bytes) / sum(jvm_memory_max_bytes)",
        },
    ]


async def test_collect_project_metrics_skips_failed_query_and_continues():
    provider = FailingThenSuccessfulPrometheusProvider()
    store = MetricWindowStore(min_samples=10)
    project = ProjectConfig(name="Demo", metric_profile="java")

    samples = await collect_project_metrics(
        project_id="demo",
        project=project,
        provider=provider,
        window_store=store,
        canonical_metrics=["conn.active", "runtime.memory_used_ratio"],
    )

    assert [sample.canonicalName for sample in samples] == [
        "runtime.memory_used_ratio"
    ]
    assert [sample.value for sample in samples] == [7.5]
    assert provider.queries == [
        {"query_type": "instant", "query": "sum(netty_connections_active_total)"},
        {
            "query_type": "instant",
            "query": "sum(jvm_memory_used_bytes) / sum(jvm_memory_max_bytes)",
        },
    ]
