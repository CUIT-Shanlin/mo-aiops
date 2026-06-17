"""Kubernetes pod logs and restart APIs."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.api.alerts import require_project_id
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
    project_id: Annotated[str, Depends(require_project_id)],
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
    namespace: str = Query(..., min_length=1),
) -> dict[str, Any]:
    try:
        action = await restart_pod(
            request,
            project_id=project_id,
            pod_name=name,
            namespace=namespace,
            operator="aiops_agent",
            operator_uid=None,
        )
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc
    return success({"id": str(action.id)})


@router.post("/api/v1/k8s/pods/{name}/restart/manual")
async def restart_pod_manual(
    name: str,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    namespace: str = Query(..., min_length=1),
) -> dict[str, Any]:
    try:
        action = await restart_pod(
            request,
            project_id=project_id,
            pod_name=name,
            namespace=namespace,
            operator="admin",
            operator_uid=current_user.user_id or current_user.role,
        )
    except APIError:
        raise
    except Exception as exc:
        raise translate_k8s_error(exc) from exc
    return success({"id": str(action.id)})
