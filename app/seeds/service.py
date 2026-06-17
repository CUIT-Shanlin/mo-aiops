"""Development demo data seeding service."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import RedisKey
from app.models.agent_run import AgentRun
from app.models.alert_event import AlertEvent
from app.models.audit_log import AuditLog
from app.models.heal_action import HealAction
from app.models.notification import Notification
from app.repositories.agent_runs import AgentRunCreate, AgentRunRepository
from app.repositories.alerts import AlertEventCreate, AlertEventRepository
from app.repositories.audit import AuditLogCreate, AuditLogRepository
from app.repositories.notifications import NotificationCreate, NotificationRepository


class SeedService:
    """Create project-scoped demo data for local development."""

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self.session = session
        self.redis = redis

    async def seed_demo(self, project_id: str, reset: bool = False) -> dict[str, int]:
        """Seed a minimal RCA/demo dataset into one project namespace."""
        if reset:
            await self._reset_project(project_id)

        now = datetime.now(UTC)
        alert = await AlertEventRepository(
            self.session, project_id
        ).create_or_update_active(
            AlertEventCreate(
                name="JVM Memory Saturation",
                fingerprint="seed-demo-jvm-memory-saturation",
                severity="critical",
                status="firing",
                service="message-service",
                namespace="prod",
                pod="message-service-0",
                labels={
                    "alertname": "JvmMemoryHigh",
                    "service": "message-service",
                    "namespace": "prod",
                    "pod": "message-service-0",
                },
                annotations={
                    "summary": "JVM heap usage is above 90%",
                    "description": "message-service JVM memory pressure detected",
                },
                fired_at=now,
                last_seen_at=now,
            )
        )

        run = await AgentRunRepository(self.session, project_id).create(
            AgentRunCreate(
                trigger_source="seed",
                alert_event_id=alert.id,
                status="completed",
                fault_service="message-service",
                anomaly_type="JVM_MEMORY",
                severity="critical",
                confidence=93.5,
                root_cause_summary=(
                    "message-service heap usage kept rising after a traffic spike; "
                    "recent logs include OutOfMemoryError."
                ),
                action_type="pod_restart",
                target_resource="deployment/message-service",
                auto_heal=True,
                risk_level="medium",
                node_states={
                    "collect_metrics": {"status": "success"},
                    "detect_anomaly": {"status": "success"},
                    "summarize_evidence": {"status": "success"},
                },
                evidence_chain={
                    "metrics": [
                        {
                            "name": "runtime.memory_used_ratio",
                            "value": 0.94,
                            "threshold": 0.90,
                        }
                    ],
                    "logs": [
                        {
                            "level": "ERROR",
                            "message": (
                                "java.lang.OutOfMemoryError: Java heap space"
                            ),
                        }
                    ],
                    "traces": [
                        {
                            "traceId": "seed-trace-1",
                            "latencyMs": 1850,
                            "service": "message-service",
                        }
                    ],
                },
                timeline=[
                    {
                        "time": now.isoformat().replace("+00:00", "Z"),
                        "event": "JVM heap exceeded 90%",
                    },
                    {
                        "time": now.isoformat().replace("+00:00", "Z"),
                        "event": "Agent identified memory pressure root cause",
                    },
                ],
                rag_results=[
                    {
                        "caseId": "seed-case-jvm-oom",
                        "title": "JVM OOM after traffic spike",
                        "similarity": 0.88,
                    }
                ],
                llm_calls_count=1,
                analyzed_logs_count=2,
                related_traces_count=1,
            )
        )
        run.finished_at = now
        alert.agent_run_id = run.id

        await NotificationRepository(self.session, project_id).create(
            NotificationCreate(
                type="alert",
                title="JVM memory alert",
                message="message-service JVM memory pressure requires attention",
            )
        )
        await AuditLogRepository(self.session, project_id).create(
            AuditLogCreate(
                operator_type="aiops_agent",
                operator_name="AIOps Seed",
                action="SEED_DEMO_DATA",
                resource_type="project",
                resource_id=project_id,
                after_state={"alertId": alert.id, "agentRunId": run.id},
                result="success",
                reason="development seed data",
            )
        )
        logs = _recent_error_logs(now)
        await self.redis.set(
            RedisKey.of(project_id, RedisKey.RECENT_ERRORS),
            json.dumps(logs),
        )
        await self.session.commit()

        return {
            "alerts": 1,
            "agentRuns": 1,
            "notifications": 1,
            "auditLogs": 1,
            "logs": len(logs),
        }

    async def _reset_project(self, project_id: str) -> None:
        for model in (Notification, HealAction, AuditLog, AgentRun, AlertEvent):
            await self.session.execute(
                delete(model).where(model.project_id == project_id)
            )


def _recent_error_logs(now: datetime) -> list[dict[str, Any]]:
    timestamp = now.isoformat().replace("+00:00", "Z")
    return [
        {
            "timestamp": timestamp,
            "level": "ERROR",
            "service": "message-service",
            "namespace": "prod",
            "pod": "message-service-0",
            "traceId": "seed-trace-1",
            "spanId": "seed-span-1",
            "message": "java.lang.OutOfMemoryError: Java heap space",
        },
        {
            "timestamp": timestamp,
            "level": "WARN",
            "service": "message-service",
            "namespace": "prod",
            "pod": "message-service-0",
            "traceId": "seed-trace-2",
            "spanId": "seed-span-2",
            "message": "GC overhead limit exceeded; heap usage remains high",
        },
    ]
