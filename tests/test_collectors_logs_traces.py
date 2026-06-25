import json

import pytest

from app.collectors.logs import (
    collect_project_logs,
    collect_project_recent_logs,
    normalize_loki_streams,
)
from app.collectors.traces import collect_project_traces, normalize_tempo_trace
from app.collectors.windows import TraceCache
from app.core.constants import RedisKey


class FakeRedis:
    def __init__(self):
        self.calls = []

    async def set(self, name, value, ex=None):
        self.calls.append((name, json.loads(value), ex))
        return True


class FakeLokiProvider:
    async def query(self, **kwargs):
        return {
            "data": {
                "result": [
                    {
                        "stream": {
                            "service": "api",
                            "namespace": "default",
                            "pod": "api-1",
                            "level": "ERROR",
                        },
                        "values": [
                            [
                                "1781623482000000000",
                                (
                                    '{"traceId":"trace-1","spanId":"span-1",'
                                    '"message":"boom"}'
                                ),
                            ]
                        ],
                    }
                ]
            }
        }


class FakeFailingLokiProvider:
    async def query(self, **kwargs):
        raise RuntimeError("loki unavailable")


class FakeBadThenGoodLokiProvider:
    async def query(self, **kwargs):
        return {
            "data": {
                "result": [
                    {"stream": "bad", "values": "bad"},
                    {
                        "stream": {"service": "worker", "level": "WARN"},
                        "values": [
                            ["bad-timestamp", '{"trace_id":"trace-2","message":"slow"}'],
                            ["1781623482000000000"],
                            [None, {"not": "a string"}],
                        ],
                    },
                ]
            }
        }


class FakeTempoProvider:
    def __init__(self):
        self.trace_ids = []

    async def query(self, **kwargs):
        self.trace_ids.append(kwargs["trace_id"])
        return {
            "traceID": kwargs["trace_id"],
            "batches": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "api"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": kwargs["trace_id"],
                                    "spanId": "span-1",
                                    "parentSpanId": "",
                                    "name": "POST /messages",
                                    "durationNanos": 12_000_000,
                                    "status": {"code": 2},
                                }
                            ]
                        }
                    ],
                }
            ],
        }


class FakeBadThenGoodTempoProvider:
    def __init__(self):
        self.trace_ids = []

    async def query(self, **kwargs):
        self.trace_ids.append(kwargs["trace_id"])
        if kwargs["trace_id"] == "bad-trace":
            return {"batches": [{"resource": [], "scopeSpans": "bad"}]}
        return {
            "batches": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "api"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {"spanId": "", "name": "missing id"},
                                {
                                    "spanId": "span-2",
                                    "name": "GET /ready",
                                    "durationNanos": "9000000",
                                    "status": {"code": 1},
                                },
                            ]
                        }
                    ],
                }
            ]
        }


class FakeFailingTempoProvider:
    def __init__(self):
        self.trace_ids = []

    async def query(self, **kwargs):
        self.trace_ids.append(kwargs["trace_id"])
        if kwargs["trace_id"] == "bad-trace":
            raise RuntimeError("tempo unavailable")
        return {
            "batches": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "api"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "spanId": "span-3",
                                    "name": "GET /live",
                                    "durationNanos": 5_000_000,
                                },
                            ]
                        }
                    ],
                }
            ]
        }


def test_normalize_loki_streams_extracts_json_message_fields():
    entries = normalize_loki_streams(
        {
            "data": {
                "result": [
                    {
                        "stream": {
                            "service": "api",
                            "namespace": "default",
                            "pod": "api-1",
                            "level": "WARN",
                        },
                        "values": [
                            [
                                "1781623482000000000",
                                '{"traceId":"t1","spanId":"s1","message":"slow"}',
                            ]
                        ],
                    }
                ]
            }
        }
    )

    assert entries[0].timestamp == "2026-06-16T15:24:42Z"
    assert entries[0].service == "api"
    assert entries[0].namespace == "default"
    assert entries[0].pod == "api-1"
    assert entries[0].level == "WARN"
    assert entries[0].traceId == "t1"
    assert entries[0].spanId == "s1"
    assert entries[0].message == "slow"


async def test_collect_project_logs_writes_recent_errors_with_project_key():
    redis = FakeRedis()

    entries, trace_ids = await collect_project_logs(
        project_id="demo",
        provider=FakeLokiProvider(),
        redis=redis,
    )

    assert [entry.traceId for entry in entries] == ["trace-1"]
    assert trace_ids == {"trace-1"}
    assert redis.calls[0][0] == RedisKey.of("demo", RedisKey.RECENT_ERRORS)
    assert redis.calls[0][1][0]["traceId"] == "trace-1"
    assert redis.calls[0][2] == 600


async def test_collect_project_logs_raises_query_errors_without_clearing_cache():
    redis = FakeRedis()

    with pytest.raises(RuntimeError, match="loki unavailable"):
        await collect_project_logs(
            project_id="demo",
            provider=FakeFailingLokiProvider(),
            redis=redis,
        )

    assert redis.calls == []


async def test_collect_project_logs_skips_bad_loki_items_and_keeps_good_entries():
    redis = FakeRedis()

    entries, trace_ids = await collect_project_logs(
        project_id="demo",
        provider=FakeBadThenGoodLokiProvider(),
        redis=redis,
    )

    assert [entry.service for entry in entries] == ["worker"]
    assert [entry.traceId for entry in entries] == ["trace-2"]
    assert trace_ids == {"trace-2"}
    assert redis.calls[0][1][0]["message"] == "slow"


class FakeAllLevelLokiProvider:
    def __init__(self):
        self.queries = []

    async def query(self, **kwargs):
        self.queries.append(kwargs.get("query"))
        return {
            "data": {
                "result": [
                    {
                        "stream": {"service": "api", "level": "INFO"},
                        "values": [
                            ["1781623482000000000", '{"message":"started ok"}'],
                        ],
                    }
                ]
            }
        }


async def test_collect_project_recent_logs_writes_all_level_cache():
    redis = FakeRedis()
    provider = FakeAllLevelLokiProvider()

    entries = await collect_project_recent_logs(
        project_id="demo",
        provider=provider,
        redis=redis,
    )

    assert [entry.level for entry in entries] == ["INFO"]
    # all-level query must not constrain level to ERROR/WARN
    assert "ERROR" not in (provider.queries[0] or "")
    assert "WARN" not in (provider.queries[0] or "")
    # default query must scope to the project
    assert 'project_id="demo"' in (provider.queries[0] or "")
    assert redis.calls[0][0] == RedisKey.of("demo", RedisKey.RECENT_LOGS)
    assert redis.calls[0][1][0]["message"] == "started ok"
    assert redis.calls[0][2] == 600


async def test_collect_project_logs_default_query_scopes_project_and_levels():
    redis = FakeRedis()
    provider = FakeAllLevelLokiProvider()

    await collect_project_logs(
        project_id="demo",
        provider=provider,
        redis=redis,
    )

    query = provider.queries[0] or ""
    assert 'project_id="demo"' in query
    assert 'level=~"ERROR|WARN"' in query


async def test_collect_project_recent_logs_raises_query_errors_without_clearing_cache():
    redis = FakeRedis()

    with pytest.raises(RuntimeError, match="loki unavailable"):
        await collect_project_recent_logs(
            project_id="demo",
            provider=FakeFailingLokiProvider(),
            redis=redis,
        )

    assert redis.calls == []


def test_normalize_tempo_trace_extracts_spans():
    spans = normalize_tempo_trace(
        "trace-1",
        {
            "batches": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "api"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "spanId": "s1",
                                    "name": "op",
                                    "status": {"code": 2},
                                }
                            ]
                        }
                    ],
                }
            ]
        },
    )

    assert spans[0].traceId == "trace-1"
    assert spans[0].spanId == "s1"
    assert spans[0].service == "api"
    assert spans[0].status == "error"


async def test_collect_project_traces_writes_trace_cache():
    cache = TraceCache(max_traces_per_project=5)
    provider = FakeTempoProvider()

    traces = await collect_project_traces(
        project_id="demo",
        trace_ids={"trace-1"},
        provider=provider,
        trace_cache=cache,
    )

    assert provider.trace_ids == ["trace-1"]
    assert "trace-1" in traces
    assert traces["trace-1"][0].status == "error"
    assert cache.get("demo", "trace-1") == traces["trace-1"]


async def test_collect_project_traces_skips_bad_spans_and_keeps_other_traces():
    cache = TraceCache(max_traces_per_project=5)
    provider = FakeBadThenGoodTempoProvider()

    traces = await collect_project_traces(
        project_id="demo",
        trace_ids=["bad-trace", "good-trace"],
        provider=provider,
        trace_cache=cache,
    )

    assert provider.trace_ids == ["bad-trace", "good-trace"]
    assert traces["bad-trace"] == []
    assert cache.get("demo", "bad-trace") is None
    assert [span.spanId for span in traces["good-trace"]] == ["span-2"]
    assert cache.get("demo", "good-trace") == traces["good-trace"]


async def test_collect_project_traces_keeps_going_without_caching_failed_queries():
    cache = TraceCache(max_traces_per_project=5)
    provider = FakeFailingTempoProvider()

    traces = await collect_project_traces(
        project_id="demo",
        trace_ids=["bad-trace", "good-trace"],
        provider=provider,
        trace_cache=cache,
    )

    assert provider.trace_ids == ["bad-trace", "good-trace"]
    assert traces["bad-trace"] == []
    assert cache.get("demo", "bad-trace") is None
    assert [span.spanId for span in traces["good-trace"]] == ["span-3"]
    assert cache.get("demo", "good-trace") == traces["good-trace"]
