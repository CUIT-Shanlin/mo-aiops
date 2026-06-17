from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel

from app.agent.rag import SimilarCaseQuery, SimilarCaseService
from app.agent.runs import AgentRunner
from app.audit.service import AuditService
from app.collectors.windows import MetricWindowStore, TraceCache
from app.core.constants import ErrorCode
from app.core.projects import get_projects_config
from app.core.security import CurrentUser, get_current_user
from app.healing.service import HealingDecision, HealingService
from app.models.agent_run import AgentRun
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.alerts import AlertEventRepository
from app.repositories.audit import AuditLogRepository
from app.repositories.healing import HealActionRepository
from app.schemas.response import APIError, success


router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
rag_router = APIRouter(prefix="/api/v1/agent/rag", tags=["agent"])


class AcceptSuggestionRequest(BaseModel):
    operatorNote: str | None = None


class RejectSuggestionRequest(BaseModel):
    reason: str


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


@router.get("/stats")
async def agent_stats(
    request: Request,
    x_project_id: str | None = Header(default=None),
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    project_id = _resolve_project_id(request, x_project_id)
    async with request.app.state.sessionmaker() as session:
        repo = AgentRunRepository(session, project_id)
        latest = await repo.latest_run()
        today_count = await repo.count_created_since(_today_start())
        pending_alerts = await AlertEventRepository(session, project_id).stats()
    return success(
        {
            "status": latest.status if latest else "idle",
            "currentTask": _current_task(latest),
            "pendingAlerts": pending_alerts["active"],
            "todayAnalysisCount": today_count,
            "adoptionRate": 0.0,
        }
    )


@router.get("/current-task")
async def current_task(
    request: Request,
    x_project_id: str | None = Header(default=None),
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    project_id = _resolve_project_id(request, x_project_id)
    async with request.app.state.sessionmaker() as session:
        latest = await AgentRunRepository(session, project_id).latest_run()
    return success(
        {
            "alertName": latest.anomaly_type if latest else None,
            "targetService": latest.fault_service if latest else None,
            "analyzedLogs": latest.analyzed_logs_count if latest else 0,
            "linkedTraces": latest.related_traces_count if latest else 0,
            "progress": 100 if latest and latest.status in {"completed", "failed"} else 0,
            "currentStep": _current_task(latest),
        }
    )


@router.get("/workflow/steps")
async def workflow_steps(
    request: Request,
    workflow_id: int | None = Query(default=None, alias="workflowId"),
    x_project_id: str | None = Header(default=None),
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    project_id = _resolve_project_id(request, x_project_id)
    async with request.app.state.sessionmaker() as session:
        repo = AgentRunRepository(session, project_id)
        run = await repo.get(workflow_id) if workflow_id is not None else await repo.latest_run()
    if run is None:
        return success([])
    return success(_workflow_steps(run))


@router.get("/latest-analysis")
async def latest_analysis(
    request: Request,
    x_project_id: str | None = Header(default=None),
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    project_id = _resolve_project_id(request, x_project_id)
    async with request.app.state.sessionmaker() as session:
        run = await AgentRunRepository(session, project_id).latest_completed()
    if run is None:
        return success(None)
    return success(_analysis_to_dict(run))


@router.post("/suggestions/{suggestion_id}/accept")
async def accept_suggestion(
    suggestion_id: int,
    payload: AcceptSuggestionRequest,
    request: Request,
    x_project_id: str | None = Header(default=None),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    project_id = _resolve_project_id(request, x_project_id)
    async with request.app.state.sessionmaker() as session:
        run_repo = AgentRunRepository(session, project_id)
        run = await run_repo.get(suggestion_id)
        if run is None:
            raise APIError(ErrorCode.NOT_FOUND, "分析建议不存在")
        if not run.action_type or not run.target_resource or run.alert_event_id is None:
            raise APIError(ErrorCode.VALIDATION_ERROR, "分析建议不可执行")
        alert_repo = AlertEventRepository(session, project_id)
        alert = await alert_repo.get(run.alert_event_id)
        if alert is None:
            raise APIError(ErrorCode.NOT_FOUND, "告警不存在")
        service = HealingService(
            HealActionRepository(session, project_id),
            alert_repo,
            AuditService(AuditLogRepository(session, project_id)),
        )
        action = await service.create_from_decision(
            alert,
            HealingDecision(
                action_type=run.action_type,
                target_resource=run.target_resource,
                risk_level=run.risk_level or "medium",
            ),
        )
        _set_suggestion_status(run, "accepted")
        await AuditService(AuditLogRepository(session, project_id)).record(
            operator_type="admin",
            operator_name=current_user.user_id or current_user.role,
            operator_uid=current_user.user_id,
            action="AGENT_SUGGESTION_ACCEPT",
            resource_type="agent_run",
            resource_id=str(run.id),
            after_state={"suggestionStatus": "accepted", "healingActionId": action.id},
            reason=payload.operatorNote,
            result="success",
        )
        await session.commit()
    return success({"healingActionId": str(action.id)})


@router.post("/suggestions/{suggestion_id}/reject")
async def reject_suggestion(
    suggestion_id: int,
    payload: RejectSuggestionRequest,
    request: Request,
    x_project_id: str | None = Header(default=None),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    project_id = _resolve_project_id(request, x_project_id)
    async with request.app.state.sessionmaker() as session:
        run = await AgentRunRepository(session, project_id).get(suggestion_id)
        if run is None:
            raise APIError(ErrorCode.NOT_FOUND, "分析建议不存在")
        _set_suggestion_status(run, "rejected")
        await AuditService(AuditLogRepository(session, project_id)).record(
            operator_type="admin",
            operator_name=current_user.user_id or current_user.role,
            operator_uid=current_user.user_id,
            action="AGENT_SUGGESTION_REJECT",
            resource_type="agent_run",
            resource_id=str(run.id),
            after_state={"suggestionStatus": "rejected"},
            reason=payload.reason,
            result="success",
        )
        await session.commit()
    return success({"success": True})


@router.get("/suggestions/{suggestion_id}/evidence")
async def suggestion_evidence(
    suggestion_id: int,
    request: Request,
    x_project_id: str | None = Header(default=None),
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    project_id = _resolve_project_id(request, x_project_id)
    async with request.app.state.sessionmaker() as session:
        run = await AgentRunRepository(session, project_id).get(suggestion_id)
    if run is None:
        raise APIError(ErrorCode.NOT_FOUND, "分析建议不存在")
    chain = run.evidence_chain or {}
    return success(
        {
            "metrics": _as_list(chain.get("metrics")),
            "logs": _as_list(chain.get("logs")),
            "traces": _as_list(chain.get("traces")),
            "confidenceScore": run.confidence or 0,
        }
    )


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


def _today_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _current_task(run: AgentRun | None) -> str:
    if run is None:
        return ""
    if run.status == "running":
        return "分析中"
    if run.status == "completed":
        return "分析完成"
    if run.status == "failed":
        return "分析失败"
    return run.status


def _workflow_steps(run: AgentRun) -> list[dict[str, Any]]:
    steps = []
    for node_id, state in (run.node_states or {}).items():
        payload = state if isinstance(state, dict) else {}
        steps.append(
            {
                "id": node_id,
                "name": node_id,
                "status": payload.get("status", "completed"),
                "duration": payload.get("duration"),
                "details": payload.get("details") or payload.get("result_summary") or "",
            }
        )
    return steps


def _analysis_to_dict(run: AgentRun) -> dict[str, Any]:
    chain = run.evidence_chain or {}
    return {
        "id": str(run.id),
        "summary": run.root_cause_summary or "",
        "rootCause": run.root_cause_summary or "",
        "confidenceScore": run.confidence or 0,
        "evidenceCount": {
            "metrics": len(_as_list(chain.get("metrics"))),
            "logs": len(_as_list(chain.get("logs"))),
            "traces": len(_as_list(chain.get("traces"))),
        },
        "affectedScope": run.fault_service or "",
        "riskNote": run.risk_level or "",
        "recommendations": [
            {
                "step": 1,
                "description": run.action_type or "无需自动处置",
            }
        ],
        "suggestionId": str(run.id),
        "suggestionStatus": (run.node_states or {}).get("suggestionStatus", "pending"),
    }


def _set_suggestion_status(run: AgentRun, status: str) -> None:
    state = dict(run.node_states or {})
    state["suggestionStatus"] = status
    run.node_states = state


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
