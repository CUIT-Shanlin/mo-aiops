from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request

from app.agent.rag import SimilarCaseQuery, SimilarCaseService
from app.agent.runs import AgentRunner
from app.collectors.windows import MetricWindowStore, TraceCache
from app.core.constants import ErrorCode
from app.core.projects import get_projects_config
from app.core.security import CurrentUser, get_current_user
from app.schemas.response import APIError, success


router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
rag_router = APIRouter(prefix="/api/v1/agent/rag", tags=["agent"])


@router.post("/trigger")
async def trigger_agent(
    request: Request,
    x_project_id: str | None = Header(default=None),
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    resolved_project_id = _resolve_project_id(
        request,
        x_project_id,
    )
    async with request.app.state.sessionmaker() as session:
        runner = AgentRunner(
            session=session,
            project_id=resolved_project_id,
            redis=request.app.state.redis,
            metric_window_store=_metric_window_store(request),
            trace_cache=_trace_cache(request),
        )
        summary = await runner.run_once(trigger_source="manual")
    return success(summary.model_dump(by_alias=True))


@rag_router.get("/similar-cases")
async def similar_cases(
    request: Request,
    alert_type: str = Query(alias="alertType"),
    service: str = Query(),
    x_project_id: str | None = Header(default=None),
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    resolved_project_id = _resolve_project_id(
        request,
        x_project_id,
    )
    async with request.app.state.sessionmaker() as session:
        cases = await SimilarCaseService(session, resolved_project_id).find_similar_cases(
            SimilarCaseQuery(alert_type=alert_type, service=service)
        )
    return success([case.model_dump() for case in cases])


def _resolve_project_id(
    request: Request,
    raw_project_id: str | None,
) -> str:
    config = getattr(request.app.state, "projects_config", None) or get_projects_config()
    if raw_project_id is None or not raw_project_id.strip():
        raise APIError(ErrorCode.NOT_FOUND, "项目不存在")
    else:
        project_id = raw_project_id
    project = config.projects.get(project_id)
    if project is None or not project.enabled:
        raise APIError(ErrorCode.NOT_FOUND, "项目不存在")
    return project_id


def _metric_window_store(request: Request) -> MetricWindowStore:
    store = getattr(request.app.state, "metric_window_store", None)
    if store is None:
        store = MetricWindowStore()
        request.app.state.metric_window_store = store
    return store


def _trace_cache(request: Request) -> TraceCache:
    cache = getattr(request.app.state, "trace_cache", None)
    if cache is None:
        cache = TraceCache()
        request.app.state.trace_cache = cache
    return cache
