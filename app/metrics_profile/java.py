from __future__ import annotations

from app.metrics_profile.base import MetricProfile
from app.metrics_profile.registry import register_profile

JAVA_PROFILE = MetricProfile(
    name="java",
    mappings={
        "conn.active": (
            "sum(netty_connections_active_total)"
        ),
        "msg.throughput": (
            "sum(rate(jvm_gc_pause_seconds_count[5m]))"
        ),
        "msg.p99_latency": (
            "histogram_quantile(0.99, sum(rate(http_server_requests_seconds_bucket[5m])) by (le))"
        ),
        "mq.backlog": (
            "sum(mq_backlog_messages)"
        ),
        "runtime.memory_used_ratio": (
            "sum(jvm_memory_used_bytes) / sum(jvm_memory_max_bytes)"
        ),
        "runtime.gc_pause": (
            "sum(rate(jvm_gc_pause_seconds_sum[5m]))"
        ),
        "http.error_rate": (
            "sum(rate(http_server_requests_seconds_count{status=~\"5..\"}[5m])) / sum(rate(http_server_requests_seconds_count[5m]))"
        ),
        "cache.hit_ratio": (
            "sum(rate(cache_hits_total[5m])) / sum(rate(cache_requests_total[5m]))"
        ),
        "db.write_tps": (
            "sum(rate(db_writes_total[5m]))"
        ),
        "sys.cpu": (
            "sum(rate(process_cpu_seconds_total[5m]))"
        ),
        "sys.memory": (
            "sum(process_resident_memory_bytes)"
        ),
        "sys.network": (
            "sum(rate(node_network_receive_bytes_total[5m]) + rate(node_network_transmit_bytes_total[5m]))"
        ),
    },
)

register_profile(JAVA_PROFILE)
