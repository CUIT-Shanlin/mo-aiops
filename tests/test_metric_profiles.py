import pytest

from app.metrics_profile import get_profile, list_profiles, register_profile
from app.metrics_profile.base import (
    MetricProfile,
    DuplicateProfileError,
    UnknownMetricError,
    UnknownProfileError,
)


def test_java_profile_auto_registered():
    assert "java" in list_profiles()


def test_java_runtime_memory_used_ratio_resolution():
    profile = get_profile("java")
    query = profile.resolve("runtime.memory_used_ratio")
    assert "jvm_memory_used_bytes" in query
    assert "jvm_memory_max_bytes" in query


def test_resolve_many_keeps_canonical_keys():
    profile = get_profile("java")
    result = profile.resolve_many(["runtime.memory_used_ratio", "http.error_rate"])

    assert set(result) == {"runtime.memory_used_ratio", "http.error_rate"}
    assert "jvm_memory_used_bytes" in result["runtime.memory_used_ratio"]


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
        "runtime.memory_used_ratio",
        "runtime.gc_pause",
        "http.error_rate",
        "sys.cpu",
        "sys.memory",
        "jvm.threads",
        "jvm.classes_loaded",
    ]


def test_register_profile_adds_profile_to_registry():
    profile = MetricProfile(name="python", mappings={"sys.cpu": "python_cpu_usage"})

    register_profile(profile)

    assert "python" in list_profiles()
    assert get_profile("python") is profile


def test_register_profile_rejects_duplicate_name():
    profile = MetricProfile(name="duplicate", mappings={"sys.cpu": "cpu_metric"})
    register_profile(profile)

    with pytest.raises(DuplicateProfileError, match="duplicate metric profile"):
        register_profile(MetricProfile(name="duplicate", mappings={"sys.cpu": "other"}))


def test_register_profile_rejects_same_object_reregistration():
    profile = MetricProfile(name="same-object", mappings={"sys.cpu": "cpu_metric"})
    register_profile(profile)

    with pytest.raises(DuplicateProfileError, match="duplicate metric profile"):
        register_profile(profile)
