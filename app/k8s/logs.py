from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from app.audit.service import AuditService
from app.core.constants import ErrorCode
from app.core.projects import ProjectConfig
from app.models.heal_action import HealAction
from app.repositories.audit import AuditLogRepository
from app.repositories.healing import HealActionCreate, HealActionRepository
from app.schemas.response import APIError


class K8sProviderNotConfiguredError(RuntimeError):
    """Raised when the project does not expose a usable K8s provider."""


def _now() -> datetime:
    return datetime.now(UTC)


def get_k8s_provider(request: Request, project_id: str) -> Any:
    factory = getattr(request.app.state, "k8s_provider_factory", None)
    if factory is not None:
        return factory(project_id)

    projects_config = getattr(request.app.state, "projects_config", None)
    project: ProjectConfig | None = None
    if projects_config is not None:
        project = projects_config.projects.get(project_id)

    create_provider = getattr(request.app.state, "create_k8s_provider", None)
    if create_provider is not None and project is not None:
        return create_provider(project_id, project)

    raise K8sProviderNotConfiguredError(f"k8s provider unavailable for project {project_id}")


async def get_pod_logs(
    request: Request,
    *,
    project_id: str,
    pod_name: str,
    namespace: str,
    tail: int,
) -> str:
    provider = get_k8s_provider(request, project_id)
    return await provider.query(
        command_type="pod_logs",
        pod_name=pod_name,
        namespace=namespace,
        tail=tail,
    )


async def stream_pod_logs(
    provider: Any,
    *,
    pod_name: str,
    namespace: str,
    tail: int,
) -> AsyncIterator[str]:
    validate_stream_support(provider)
    stream = provider.stream_pod_logs
    async for line in stream(
        pod_name=pod_name,
        namespace=namespace,
        tail=tail,
    ):
        yield line


def validate_stream_support(provider: Any) -> None:
    stream = getattr(provider, "stream_pod_logs", None)
    if stream is None:
        raise K8sProviderNotConfiguredError("k8s provider does not support log streaming")


async def restart_pod(
    request: Request,
    *,
    project_id: str,
    pod_name: str,
    namespace: str,
    operator: str,
    operator_uid: str | None,
    reason: str | None = None,
) -> HealAction:
    provider = get_k8s_provider(request, project_id)

    async with request.app.state.sessionmaker() as session:
        repository = HealActionRepository(session, project_id)
        audit_service = AuditService(AuditLogRepository(session, project_id))
        action = await repository.create(
            HealActionCreate(
                action_type="pod_restart",
                target_resource=f"pod/{pod_name}",
                target_namespace=namespace,
                status="executing",
                risk_level="low",
                operator=operator,
                result_message="pod restart requested",
                executed_at=_now(),
            )
        )
        await audit_service.record(
            operator_type=operator,
            operator_name=operator_uid or operator,
            operator_uid=operator_uid,
            action="POD_RESTART",
            resource_type="heal_action",
            resource_id=str(action.id),
            before_state=None,
            after_state={
                "actionType": action.action_type,
                "targetResource": action.target_resource,
                "namespace": action.target_namespace,
                "status": action.status,
                "operator": action.operator,
                "reason": reason,
            },
            reason=reason,
            result="requested",
        )
        await session.commit()

    try:
        await provider.notify(
            action="pod_restart",
            pod_name=pod_name,
            namespace=namespace,
        )
    except Exception as exc:
        async with request.app.state.sessionmaker() as session:
            repository = HealActionRepository(session, project_id)
            audit_service = AuditService(AuditLogRepository(session, project_id))
            persisted = await repository.get_for_update(action.id)
            if persisted is not None:
                persisted.status = "failed"
                persisted.result_message = str(exc)
                persisted.finished_at = _now()
                persisted.updated_at = persisted.finished_at
                await audit_service.record(
                    operator_type=operator,
                    operator_name=operator_uid or operator,
                    operator_uid=operator_uid,
                    action="POD_RESTART",
                    resource_type="heal_action",
                    resource_id=str(persisted.id),
                    before_state={"status": "executing"},
                    after_state={
                        "status": persisted.status,
                        "result": persisted.result_message,
                    },
                    reason=None,
                    result="failed",
                )
            await session.commit()
        raise
    else:
        async with request.app.state.sessionmaker() as session:
            repository = HealActionRepository(session, project_id)
            audit_service = AuditService(AuditLogRepository(session, project_id))
            persisted = await repository.get_for_update(action.id)
            if persisted is not None:
                persisted.status = "success"
                persisted.result_message = "pod restart completed"
                persisted.finished_at = _now()
                persisted.updated_at = persisted.finished_at
                await audit_service.record(
                    operator_type=operator,
                    operator_name=operator_uid or operator,
                    operator_uid=operator_uid,
                    action="POD_RESTART",
                    resource_type="heal_action",
                    resource_id=str(persisted.id),
                    before_state={"status": "executing"},
                    after_state={
                        "status": persisted.status,
                        "result": persisted.result_message,
                    },
                    reason=None,
                    result="success",
                )
                await session.commit()
                return persisted
    raise RuntimeError("unreachable restart state")


def translate_k8s_error(exc: Exception) -> APIError:
    if isinstance(exc, K8sProviderNotConfiguredError):
        return APIError(ErrorCode.K8S_ERROR, "Kubernetes Provider 未配置")
    return APIError(ErrorCode.K8S_ERROR, "Kubernetes API 调用失败")
