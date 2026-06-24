"""Kubernetes pod logs and restart APIs."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.api.alerts import require_project_id
from app.core.constants import ErrorCode
from app.core.logging import set_project_id
from app.core.projects import get_projects_config
from app.core.security import CurrentUser, get_current_user
from app.k8s.logs import (
    get_k8s_provider,
    get_pod_logs,
    restart_pod,
    stream_pod_logs,
    translate_k8s_error,
    validate_stream_support,
)
from app.schemas.response import APIError, success


router = APIRouter(tags=["k8s"])


class PodRestartRequest(BaseModel):
    namespace: str | None = Field(default=None, min_length=1)
    reason: str | None = None


async def require_project_id_for_sse(
    request: Request,
    x_project_id: Annotated[str | None, Header()] = None,
    project_id: str | None = Query(default=None),
) -> str:
    if x_project_id and project_id and x_project_id != project_id:
        raise APIError(ErrorCode.VALIDATION_ERROR, "X-Project-Id 与 project_id 不一致")
    effective_project_id = x_project_id or project_id
    if not effective_project_id:
        raise APIError(ErrorCode.NOT_FOUND, "项目不存在")
    config = getattr(request.app.state, "projects_config", None) or get_projects_config()
    project = config.projects.get(effective_project_id)
    if project is None or not project.enabled:
        raise APIError(ErrorCode.NOT_FOUND, "项目不存在")
    set_project_id(effective_project_id)
    return effective_project_id


@router.get("/api/v1/k8s/overview")
async def k8s_overview(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    try:
        pods = _items(await _query_k8s(request, project_id, "list_pods"))
        nodes = _items(await _query_k8s(request, project_id, "list_nodes"))
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc
    return success(
        {
            "nodeCount": len(nodes),
            "readyNodeCount": sum(1 for node in nodes if _node_ready(node)),
            "podCount": len(pods),
            "runningPodCount": sum(1 for pod in pods if _pod_phase(pod) == "Running"),
            "crashLoopCount": sum(1 for pod in pods if _pod_crash_loop(pod)),
            "clusterCpuUsage": 0,
            "totalCores": 0,
            "usedCores": 0,
        }
    )


@router.get("/api/v1/k8s/pods")
async def k8s_pods(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    namespace: str | None = Query(default=None),
    status: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    try:
        pods = [
            _pod_to_dict(pod)
            for pod in _items(
                await _query_k8s(request, project_id, "list_pods", namespace=namespace)
            )
        ]
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc
    if status:
        pods = [pod for pod in pods if pod["status"] == status]
    if keyword:
        pods = [pod for pod in pods if keyword in pod["name"]]
    offset = (page - 1) * page_size
    return success(
        {
            "total": len(pods),
            "page": page,
            "pageSize": page_size,
            "items": pods[offset : offset + page_size],
        }
    )


@router.get("/api/v1/k8s/nodes")
async def k8s_nodes(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        nodes = [_node_to_dict(node) for node in _items(await _query_k8s(request, project_id, "list_nodes"))]
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc
    if status:
        nodes = [node for node in nodes if node["status"] == status]
    return success(nodes)


@router.get("/api/v1/k8s/namespaces")
async def k8s_namespaces(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    try:
        namespaces = [
            _metadata(item).get("name", "")
            for item in _items(await _query_k8s(request, project_id, "list_namespaces"))
        ]
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc
    return success([name for name in namespaces if name])


@router.get("/api/v1/k8s/deployments")
async def k8s_deployments(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    namespace: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        deployments = [
            _metadata(item).get("name", "")
            for item in _items(
                await _query_k8s(
                    request,
                    project_id,
                    "list_deployments",
                    namespace=namespace,
                )
            )
        ]
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc
    return success([name for name in deployments if name])


@router.get("/api/v1/k8s/pods/{name}/logs")
async def pod_logs(
    name: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    namespace: str = Query(..., min_length=1),
    tail: int = Query(default=100, ge=1, le=10000),
) -> PlainTextResponse:
    try:
        logs = await get_pod_logs(
            request,
            project_id=project_id,
            pod_name=name,
            namespace=namespace,
            tail=tail,
        )
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc
    return PlainTextResponse(logs)


@router.get("/api/v1/k8s/pods/{name}/logs/stream")
async def pod_logs_stream(
    name: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id_for_sse)],
    namespace: str = Query(..., min_length=1),
    tail: int = Query(default=100, ge=1, le=10000),
) -> StreamingResponse:
    try:
        provider = get_k8s_provider(request, project_id)
        validate_stream_support(provider)
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for line in stream_pod_logs(
                provider,
                pod_name=name,
                namespace=namespace,
                tail=tail,
            ):
                yield f"data: {line}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {translate_k8s_error(exc).message}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/v1/k8s/pods/{name}/restart")
async def restart_pod_by_agent(
    name: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    namespace: str | None = Query(default=None, min_length=1),
    payload: PodRestartRequest | None = Body(default=None),
) -> dict[str, Any]:
    resolved_namespace = _resolve_namespace(payload, namespace)
    try:
        action = await restart_pod(
            request,
            project_id=project_id,
            pod_name=name,
            namespace=resolved_namespace,
            operator="aiops_agent",
            operator_uid=None,
            reason=payload.reason if payload else None,
        )
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc
    return success({"success": True, "taskId": str(action.id), "id": str(action.id)})


@router.post("/api/v1/k8s/pods/{name}/restart/manual")
async def restart_pod_manual(
    name: str,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    namespace: str | None = Query(default=None, min_length=1),
    payload: PodRestartRequest | None = Body(default=None),
) -> dict[str, Any]:
    resolved_namespace = _resolve_namespace(payload, namespace)
    try:
        action = await restart_pod(
            request,
            project_id=project_id,
            pod_name=name,
            namespace=resolved_namespace,
            operator="admin",
            operator_uid=current_user.user_id or current_user.role,
            reason=payload.reason if payload else None,
        )
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc
    return success({"success": True, "taskId": str(action.id), "id": str(action.id)})


async def _query_k8s(
    request: Request,
    project_id: str,
    command_type: str,
    **kwargs: Any,
) -> Any:
    provider = get_k8s_provider(request, project_id)
    return await provider.query(command_type=command_type, **kwargs)


def _resolve_namespace(payload: PodRestartRequest | None, query_namespace: str | None) -> str:
    namespace = payload.namespace if payload and payload.namespace else query_namespace
    if not namespace:
        raise APIError(40022, "参数校验失败")
    return namespace


def _items(result: Any) -> list[Any]:
    if isinstance(result, dict):
        return list(result.get("items") or [])
    return list(getattr(result, "items", []) or [])


def _metadata(item: Any) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item, dict) else getattr(item, "metadata", None)
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return metadata
    return {
        "name": getattr(metadata, "name", None),
        "namespace": getattr(metadata, "namespace", None),
        "labels": getattr(metadata, "labels", None),
    }


def _status(item: Any) -> dict[str, Any]:
    status = item.get("status") if isinstance(item, dict) else getattr(item, "status", None)
    if status is None:
        return {}
    if isinstance(status, dict):
        return status
    return {
        "phase": getattr(status, "phase", None),
        "pod_ip": getattr(status, "pod_ip", None),
        "conditions": getattr(status, "conditions", None),
        "container_statuses": getattr(status, "container_statuses", None),
    }


def _pod_to_dict(pod: Any) -> dict[str, Any]:
    metadata = _metadata(pod)
    status = _status(pod)
    container_statuses = status.get("container_statuses") or []
    restarts = sum(int(_get(container, "restart_count") or 0) for container in container_statuses)
    ready = all(bool(_get(container, "ready")) for container in container_statuses) if container_statuses else False
    return {
        "name": metadata.get("name", ""),
        "namespace": metadata.get("namespace", ""),
        "deployment": (metadata.get("labels") or {}).get("app"),
        "status": "CrashLoopBackOff" if _pod_crash_loop(pod) else _pod_phase(pod),
        "podIP": status.get("pod_ip"),
        "ready": ready,
        "restartCount": restarts,
    }


def _node_to_dict(node: Any) -> dict[str, Any]:
    metadata = _metadata(node)
    return {
        "name": metadata.get("name", ""),
        "status": "Ready" if _node_ready(node) else "NotReady",
        "cpuUsage": 0,
        "memoryUsage": 0,
        "podCount": 0,
    }


def _pod_phase(pod: Any) -> str:
    return str(_status(pod).get("phase") or "")


def _pod_crash_loop(pod: Any) -> bool:
    for container in _status(pod).get("container_statuses") or []:
        state = _get(container, "state") or {}
        waiting = state.get("waiting") if isinstance(state, dict) else getattr(state, "waiting", None)
        reason = waiting.get("reason") if isinstance(waiting, dict) else getattr(waiting, "reason", None)
        if reason == "CrashLoopBackOff":
            return True
    return False


def _node_ready(node: Any) -> bool:
    for condition in _status(node).get("conditions") or []:
        if _get(condition, "type") == "Ready":
            return _get(condition, "status") == "True"
    return False


def _get(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)
