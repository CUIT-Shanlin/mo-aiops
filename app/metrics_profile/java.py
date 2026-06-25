from __future__ import annotations

from app.metrics_profile.base import MetricProfile
from app.metrics_profile.registry import register_profile

JAVA_PROFILE = MetricProfile(
    name="java",
    mappings={
        "runtime.memory_used_ratio": (
            "sum(jvm_memory_used_bytes{namespace='mochat'}) / sum(jvm_memory_max_bytes{namespace='mochat'})"
        ),
        "runtime.gc_pause": (
            "sum(rate(jvm_gc_pause_seconds_sum{namespace='mochat'}[5m]))"
        ),
        "http.error_rate": (
            "sum(rate(http_server_requests_seconds_count{namespace='mochat',status=~'5..'}[5m])) / sum(rate(http_server_requests_seconds_count{namespace='mochat'}[5m]))"
        ),
        "sys.cpu": (
            "sum(rate(process_cpu_seconds_total{namespace='mochat'}[5m]))"
        ),
        "sys.memory": (
            "sum(process_resident_memory_bytes{namespace='mochat'})"
        ),
        "jvm.threads": (
            "sum(jvm_threads_live_threads{namespace='mochat'})"
        ),
        "jvm.classes_loaded": (
            "sum(jvm_classes_loaded_classes{namespace='mochat'})"
        ),
    },
)

register_profile(JAVA_PROFILE)
