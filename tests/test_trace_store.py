import json

import pytest

from app.collectors.models import SpanSummary
from app.collectors.trace_store import (
    deserialize_traces,
    load_trace_snapshot,
    save_trace_snapshot,
    serialize_traces,
)
from app.collectors.windows import TraceCache
from app.core.constants import RedisKey


class FakeRedis:
    def __init__(self, store=None):
        self.store = store or {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


def _spans():
    return [
        SpanSummary(traceId="t1", spanId="s1", service="access-gateway", name="recv", durationMs=10),
        SpanSummary(traceId="t1", spanId="s2", parentSpanId="s1", service="api-service", name="route", durationMs=20, status="error"),
    ]


def test_serialize_roundtrip():
    snapshot = [("t1", _spans())]
    restored = deserialize_traces(serialize_traces(snapshot))
    assert restored[0][0] == "t1"
    assert restored[0][1][1].service == "api-service"
    assert restored[0][1][1].status == "error"


def test_deserialize_bad_value_returns_empty():
    assert deserialize_traces("not-json") == []
    assert deserialize_traces(None) == []


async def test_save_and_load_from_redis():
    redis = FakeRedis()
    await save_trace_snapshot(redis, "mochat-prod", [("t1", _spans())])
    assert RedisKey.of("mochat-prod", RedisKey.TRACES) in redis.store
    loaded = await load_trace_snapshot(redis, None, "mochat-prod")
    assert loaded[0][0] == "t1"


async def test_load_prefers_memory_over_redis():
    redis = FakeRedis()
    await save_trace_snapshot(redis, "mochat-prod", [("redis-trace", _spans())])
    cache = TraceCache()
    cache.put("mochat-prod", "mem-trace", _spans())
    loaded = await load_trace_snapshot(redis, cache, "mochat-prod")
    assert [tid for tid, _ in loaded] == ["mem-trace"]
