import pytest

from app.metrics_profile import get_profile, list_profiles
from app.metrics_profile.base import UnknownMetricError, UnknownProfileError


def test_java_profile_auto_registered():
    assert "java" in list_profiles()


def test_java_runtime_memory_used_ratio_resolution():
    profile = get_profile("java")
    query = profile.resolve("runtime.memory_used_ratio")
    assert "jvm_memory_used_bytes" in query
    assert "jvm_memory_max_bytes" in query


def test_resolve_many_keeps_canonical_keys():
    profile = get_profile("java")
    result = profile.resolve_many(["conn.active", "http.error_rate"])

    assert set(result) == {"conn.active", "http.error_rate"}
    assert "netty_connections_active_total" in result["conn.active"]


def test_unknown_profile_error_message():
    with pytest.raises(UnknownProfileError, match="unknown metric profile"):
        get_profile("python")


def test_unknown_metric_error_message():
    profile = get_profile("java")
    with pytest.raises(UnknownMetricError, match="unknown canonical metric"):
        profile.resolve("runtime.unknown")


def test_java_profile_covers_expected_metrics():
    profile = get_profile("java")
    assert profile.list_metrics() == [
        "conn.active",
        "msg.throughput",
        "msg.p99_latency",
        "mq.backlog",
        "runtime.memory_used_ratio",
        "runtime.gc_pause",
        "http.error_rate",
        "cache.hit_ratio",
        "db.write_tps",
        "sys.cpu",
        "sys.memory",
        "sys.network",
    ]
