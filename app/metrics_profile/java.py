from __future__ import annotations

from app.metrics_profile.base import MetricProfile
from app.metrics_profile.registry import register_profile

# 业务指标 (msg.throughput, conn.active, msg.p99_latency, mq.backlog,
# db.write_tps, cache.hit_ratio, sys.network) 暂未映射：mo-chat 当前
# 未通过 Prometheus 暴露这些自定义业务指标，仅暴露 Micrometer 自带的
# JVM/HTTP 指标。待 mo-chat 侧补充业务指标后，在此追加映射即可，
# 采集与分析逻辑无需改动（指标经 profile 解析，见 AGENTS.md §2.2）。

JAVA_PROFILE = MetricProfile(
    name="java",
    mappings={
        "runtime.memory_used_ratio": (
            "sum(jvm_memory_used_bytes{namespace='mochat',area='heap'}) "
            "/ sum(jvm_memory_max_bytes{namespace='mochat',area='heap'}) * 100"
        ),
        "runtime.gc_pause": (
            "sum(rate(jvm_gc_pause_seconds_sum{namespace='mochat'}[5m]))"
        ),
        "http.error_rate": (
            "(sum(rate(http_server_requests_seconds_count{namespace='mochat',status=~'5..'}[5m])) "
            "/ clamp_min(sum(rate(http_server_requests_seconds_count{namespace='mochat'}[5m])), 1)) "
            "or vector(0)"
        ),
        "sys.cpu": (
            "avg(process_cpu_usage{namespace='mochat'}) * 100"
        ),
        "sys.memory": (
            "sum(jvm_memory_used_bytes{namespace='mochat'}) "
            "/ sum(jvm_memory_committed_bytes{namespace='mochat'}) * 100"
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
