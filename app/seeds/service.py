"""Development demo data seeding service (bulk demo dataset)."""
from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.metric_store import save_metric_snapshot
from app.collectors.trace_store import save_trace_snapshot
from app.core.constants import RedisKey
from app.models.agent_run import AgentRun
from app.models.alert_event import AlertEvent
from app.models.audit_log import AuditLog
from app.models.heal_action import HealAction
from app.models.notification import Notification
from app.repositories.agent_runs import AgentRunCreate, AgentRunRepository
from app.repositories.alerts import AlertEventCreate, AlertEventRepository
from app.repositories.audit import AuditLogCreate, AuditLogRepository
from app.repositories.healing import HealActionCreate, HealActionRepository
from app.repositories.notifications import NotificationCreate, NotificationRepository

SERVICES = [
    "access-gateway",
    "api-service",
    "call-service",
    "message-service",
    "multimedia-service",
    "persistence-service",
]

# 调用链父→子（构建 trace span 树用）
CALL_EDGES = [
    ("access-gateway", "api-service"),
    ("api-service", "message-service"),
    ("api-service", "call-service"),
    ("api-service", "multimedia-service"),
    ("message-service", "persistence-service"),
    ("call-service", "persistence-service"),
    ("multimedia-service", "persistence-service"),
]

ANOMALY_TYPES = [
    ("JVM_MEMORY", "critical", "JVM heap 使用率持续高于 90%，疑似内存泄漏"),
    ("HTTP_5XX", "critical", "HTTP 5xx 错误率突增，下游依赖异常"),
    ("HIGH_LATENCY", "warning", "P99 延迟超过阈值，线程池接近饱和"),
    ("GC_PAUSE", "warning", "GC 停顿时间偏高，吞吐下降"),
    ("CPU_SATURATION", "warning", "CPU 使用率持续偏高"),
    ("MQ_BACKLOG", "info", "消息队列积压增长"),
]

ACTION_TYPES = ["pod_restart", "hpa_scale", "rolling_restart"]
HEAL_STATUSES = ["success", "verified", "failed", "pending", "executing", "waiting_approval"]
LOG_LEVELS = ["INFO", "INFO", "INFO", "WARN", "ERROR", "DEBUG"]

ERROR_MESSAGES = {
    "JVM_MEMORY": "java.lang.OutOfMemoryError: Java heap space",
    "HTTP_5XX": "Internal Server Error: downstream call failed with 503",
    "HIGH_LATENCY": "request processing exceeded 1000ms threshold",
    "GC_PAUSE": "GC overhead limit exceeded; heap usage remains high",
    "CPU_SATURATION": "CPU usage sustained above 90% for 5m",
    "MQ_BACKLOG": "consumer lag increased to 12000 messages",
}

METRIC_SNAPSHOT = {
    "sys.cpu": 72.4,
    "sys.memory": 81.2,
    "sys.network": 240.5,
    "runtime.memory_used_ratio": 88.7,
    "runtime.gc_pause": 180.0,
    "msg.throughput": 12600.0,
    "conn.active": 8800.0,
    "msg.p99_latency": 73.0,
    "db.write_tps": 3400.0,
    "cache.hit_ratio": 96.3,
    "http.error_rate": 1.8,
    "jvm.threads": 420.0,
    "jvm.classes_loaded": 18900.0,
    "mq.backlog": 12.0,
}


class SeedService:
    """Create project-scoped bulk demo data for local development / demo."""

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis
        self.rng = random.Random(42)

    async def seed_demo(self, project_id: str, reset: bool = False) -> dict[str, int]:
        if reset:
            await self._reset_project(project_id)

        now = datetime.now(UTC)
        alerts = await self._seed_alerts(project_id, now)
        runs = await self._seed_agent_runs(project_id, now, alerts)
        heal_count = await self._seed_heal_actions(project_id, now, alerts)
        notif_count = await self._seed_notifications(project_id, now)
        audit_count = await self._seed_audit_logs(project_id, now)
        await self.session.commit()

        logs = self._build_logs(now)
        traces = self._build_traces(now)
        await self.redis.set(
            RedisKey.of(project_id, RedisKey.RECENT_LOGS),
            json.dumps(logs, ensure_ascii=False),
        )
        await self.redis.set(
            RedisKey.of(project_id, RedisKey.RECENT_ERRORS),
            json.dumps([log for log in logs if log["level"] in ("ERROR", "WARN")], ensure_ascii=False),
        )
        await save_trace_snapshot(self.redis, project_id, traces)
        await save_metric_snapshot(self.redis, project_id, METRIC_SNAPSHOT)

        return {
            "alerts": len(alerts),
            "agentRuns": len(runs),
            "healActions": heal_count,
            "notifications": notif_count,
            "auditLogs": audit_count,
            "logs": len(logs),
            "traces": len(traces),
            "metrics": len(METRIC_SNAPSHOT),
        }

    async def _reset_project(self, project_id: str) -> None:
        for model in (Notification, HealAction, AuditLog, AgentRun, AlertEvent):
            await self.session.execute(
                delete(model).where(model.project_id == project_id)
            )
        await self.session.commit()

    async def _seed_alerts(self, project_id: str, now: datetime) -> list[AlertEvent]:
        repo = AlertEventRepository(self.session, project_id)
        alerts: list[AlertEvent] = []
        # 活跃告警：2 critical + 2 warning + 4 其他活跃态
        active_specs = [
            ("firing", "critical"),
            ("processing", "critical"),
            ("firing", "warning"),
            ("healing", "warning"),
            ("firing", "info"),
            ("acknowledged", "warning"),
            ("processing", "info"),
            ("firing", "info"),
        ]
        for idx, (status, severity) in enumerate(active_specs):
            service = SERVICES[idx % len(SERVICES)]
            anomaly = self._anomaly_for(severity)
            fired = now - timedelta(minutes=self.rng.randint(5, 240))
            alert = await repo.create(
                AlertEventCreate(
                    name=f"{anomaly[0]} on {service}",
                    fingerprint=f"seed-active-{idx}",
                    severity=severity,
                    status=status,
                    service=service,
                    namespace="mochat",
                    pod=f"{service}-{self.rng.randint(0, 2)}",
                    labels={"alertname": anomaly[0], "service": service, "namespace": "mochat"},
                    annotations={"summary": anomaly[2], "description": anomaly[2]},
                    fired_at=fired,
                    last_seen_at=now,
                    alert_count=self.rng.randint(1, 8),
                )
            )
            alerts.append(alert)
        # 历史已恢复告警 ~115 条
        for idx in range(115):
            service = SERVICES[idx % len(SERVICES)]
            severity = self.rng.choice(["critical", "warning", "warning", "info", "info"])
            anomaly = self._anomaly_for(severity)
            fired = now - timedelta(
                hours=self.rng.randint(0, 23),
                minutes=self.rng.randint(0, 59),
            )
            resolved = fired + timedelta(minutes=self.rng.randint(3, 90))
            alert = await repo.create(
                AlertEventCreate(
                    name=f"{anomaly[0]} on {service}",
                    fingerprint=f"seed-resolved-{idx}",
                    severity=severity,
                    status="resolved",
                    service=service,
                    namespace="mochat",
                    pod=f"{service}-{self.rng.randint(0, 2)}",
                    labels={"alertname": anomaly[0], "service": service, "namespace": "mochat"},
                    annotations={"summary": anomaly[2], "description": anomaly[2]},
                    fired_at=fired,
                    last_seen_at=resolved,
                    resolved_at=resolved,
                    alert_count=self.rng.randint(1, 5),
                )
            )
            alerts.append(alert)
        return alerts

    def _anomaly_for(self, severity: str) -> tuple[str, str, str]:
        candidates = [item for item in ANOMALY_TYPES if item[1] == severity]
        return self.rng.choice(candidates or ANOMALY_TYPES)

    async def _seed_agent_runs(
        self,
        project_id: str,
        now: datetime,
        alerts: list[AlertEvent],
    ) -> list[AgentRun]:
        repo = AgentRunRepository(self.session, project_id)
        runs: list[AgentRun] = []
        # 关联前 60 条告警生成 RCA/Agent 记录
        for idx, alert in enumerate(alerts[:60]):
            anomaly_name = str(alert.labels.get("alertname", "JVM_MEMORY"))
            status = "completed"
            if idx % 17 == 0:
                status = "failed"
            elif idx % 23 == 0:
                status = "running"
            confidence = round(self.rng.uniform(72.0, 98.0), 1)
            started = alert.fired_at + timedelta(seconds=self.rng.randint(10, 120))
            trace_id = f"seed-trace-{idx:03d}"
            run = await repo.create(
                AgentRunCreate(
                    trigger_source=self.rng.choice(["alert", "manual", "scheduled"]),
                    alert_event_id=alert.id,
                    status=status,
                    fault_service=alert.service,
                    anomaly_type=anomaly_name,
                    severity=alert.severity,
                    confidence=confidence,
                    root_cause_summary=str(alert.annotations.get("summary", "")),
                    action_type=self.rng.choice(ACTION_TYPES),
                    target_resource=f"deployment/{alert.service}",
                    auto_heal=self.rng.random() > 0.4,
                    risk_level=self.rng.choice(["low", "medium", "high"]),
                    node_states={
                        "collect_metrics": {"status": "success", "duration": self.rng.randint(80, 400)},
                        "detect_anomaly": {"status": "success", "duration": self.rng.randint(80, 400)},
                        "analyze_logs": {"status": "success", "duration": self.rng.randint(80, 400)},
                        "correlate_traces": {"status": "success", "duration": self.rng.randint(80, 400)},
                        "summarize_evidence": {"status": "success", "duration": self.rng.randint(80, 400)},
                        "recommended_actions": [
                            {
                                "step": 1,
                                "description": f"重启 {alert.service}",
                                "actionType": "pod_restart",
                                "targetResource": f"deployment/{alert.service}",
                                "riskLevel": "medium",
                            },
                            {
                                "step": 2,
                                "description": f"对 {alert.service} 执行 HPA 扩容",
                                "actionType": "hpa_scale",
                                "targetResource": f"deployment/{alert.service}",
                                "riskLevel": "low",
                            },
                        ],
                    },
                    evidence_chain={
                        "metrics": [
                            {"name": "runtime.memory_used_ratio", "value": round(self.rng.uniform(0.85, 0.98), 2), "threshold": 0.90},
                            {"name": "http.error_rate", "value": round(self.rng.uniform(0.01, 0.08), 3), "threshold": 0.05},
                        ],
                        "logs": [
                            {"level": "ERROR", "service": alert.service, "message": ERROR_MESSAGES.get(anomaly_name, "error")},
                        ],
                        "traces": [
                            {"traceId": trace_id, "latencyMs": self.rng.randint(800, 2400), "service": alert.service},
                        ],
                    },
                    timeline=[
                        {"time": started.isoformat().replace("+00:00", "Z"), "event": f"{anomaly_name} 触发"},
                        {"time": (started + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"), "event": "Agent 完成根因定位", "status": status},
                    ],
                    rag_results=[
                        {"caseId": f"case-{anomaly_name.lower()}", "title": f"{anomaly_name} 历史案例", "similarity": round(self.rng.uniform(0.7, 0.95), 2)},
                    ],
                    llm_calls_count=self.rng.randint(1, 5),
                    analyzed_logs_count=self.rng.randint(20, 200),
                    related_traces_count=self.rng.randint(1, 12),
                )
            )
            if status == "completed":
                run.finished_at = started + timedelta(seconds=self.rng.randint(20, 90))
            alert.agent_run_id = run.id
            runs.append(run)
        return runs

    async def _seed_heal_actions(
        self,
        project_id: str,
        now: datetime,
        alerts: list[AlertEvent],
    ) -> int:
        repo = HealActionRepository(self.session, project_id)
        count = 0
        # 为前 80 条告警各生成一个动作，动作类型与告警绑定避免唯一约束冲突
        for idx, alert in enumerate(alerts[:80]):
            status = HEAL_STATUSES[idx % len(HEAL_STATUSES)]
            action_type = ACTION_TYPES[idx % len(ACTION_TYPES)]
            executed = alert.fired_at + timedelta(minutes=self.rng.randint(1, 20))
            finished = executed + timedelta(seconds=self.rng.randint(10, 120))
            await repo.create(
                HealActionCreate(
                    action_type=action_type,
                    target_resource=f"deployment/{alert.service}",
                    target_namespace="mochat",
                    status=status,
                    risk_level=self.rng.choice(["low", "medium", "high"]),
                    operator="aiops_agent",
                    alert_event_id=alert.id,
                    approved_by="admin" if status in ("success", "verified") else None,
                    result_message="recovered" if status in ("success", "verified") else ("failed" if status == "failed" else None),
                    retry_count=self.rng.randint(0, 2),
                    executed_at=executed if status not in ("pending", "waiting_approval") else None,
                    finished_at=finished if status in ("success", "verified", "failed") else None,
                )
            )
            count += 1
        return count

    async def _seed_notifications(self, project_id: str, now: datetime) -> int:
        repo = NotificationRepository(self.session, project_id)
        types = ["alert", "healing", "system", "agent"]
        count = 0
        for idx in range(40):
            service = SERVICES[idx % len(SERVICES)]
            await repo.create(
                NotificationCreate(
                    type=types[idx % len(types)],
                    title=f"{service} 通知 #{idx}",
                    message=f"{service} 在 {idx} 分钟前产生一条 {types[idx % len(types)]} 通知",
                    read=self.rng.random() > 0.5,
                )
            )
            count += 1
        return count

    async def _seed_audit_logs(self, project_id: str, now: datetime) -> int:
        repo = AuditLogRepository(self.session, project_id)
        actions = [
            ("AGENT_SUGGESTION_ACCEPT", "agent_run"),
            ("AGENT_SUGGESTION_REJECT", "agent_run"),
            ("HEAL_ACTION_APPROVE", "heal_action"),
            ("HEAL_ACTION_EXECUTE", "heal_action"),
            ("ALERT_ACKNOWLEDGE", "alert"),
        ]
        count = 0
        for idx in range(100):
            action, resource_type = actions[idx % len(actions)]
            await repo.create(
                AuditLogCreate(
                    operator_type=self.rng.choice(["admin", "aiops_agent"]),
                    operator_name=self.rng.choice(["admin", "AIOps Agent"]),
                    operator_uid="u1",
                    action=action,
                    resource_type=resource_type,
                    resource_id=str(self.rng.randint(1, 120)),
                    after_state={"note": f"seed audit {idx}"},
                    result=self.rng.choice(["success", "success", "success", "failed"]),
                    reason="demo seed",
                )
            )
            count += 1
        return count

    def _build_logs(self, now: datetime) -> list[dict[str, Any]]:
        logs: list[dict[str, Any]] = []
        for idx in range(300):
            service = SERVICES[idx % len(SERVICES)]
            level = LOG_LEVELS[idx % len(LOG_LEVELS)]
            ts = now - timedelta(minutes=self.rng.randint(0, 24 * 60))
            trace_id = f"seed-trace-{self.rng.randint(0, 79):03d}"
            if level == "ERROR":
                message = self.rng.choice(list(ERROR_MESSAGES.values()))
            elif level == "WARN":
                message = "latency elevated; approaching threshold"
            else:
                message = f"{service} handled request ok"
            logs.append(
                {
                    "timestamp": ts.isoformat().replace("+00:00", "Z"),
                    "level": level,
                    "service": service,
                    "namespace": "mochat",
                    "pod": f"{service}-{self.rng.randint(0, 2)}",
                    "traceId": trace_id,
                    "spanId": f"span-{idx}",
                    "message": message,
                }
            )
        logs.sort(key=lambda item: item["timestamp"], reverse=True)
        return logs

    def _build_traces(self, now: datetime):
        from app.collectors.models import SpanSummary

        traces: list[tuple[str, list[SpanSummary]]] = []
        for idx in range(80):
            trace_id = f"seed-trace-{idx:03d}"
            spans: list[SpanSummary] = []
            root = "access-gateway"
            root_span_id = f"{trace_id}-root"
            has_error = idx % 6 == 0
            spans.append(
                SpanSummary(
                    traceId=trace_id,
                    spanId=root_span_id,
                    parentSpanId=None,
                    service=root,
                    name=f"{root}.handle",
                    durationMs=float(self.rng.randint(5, 40)),
                    status="success",
                )
            )
            parent_map = {root: root_span_id}
            for edge_idx, (parent, child) in enumerate(CALL_EDGES):
                if parent not in parent_map:
                    continue
                span_id = f"{trace_id}-{edge_idx}"
                is_error_span = has_error and child == "persistence-service"
                spans.append(
                    SpanSummary(
                        traceId=trace_id,
                        spanId=span_id,
                        parentSpanId=parent_map[parent],
                        service=child,
                        name=f"{child}.call",
                        durationMs=float(self.rng.randint(10, 300)),
                        status="error" if is_error_span else "success",
                    )
                )
                parent_map.setdefault(child, span_id)
            traces.append((trace_id, spans))
        return traces
