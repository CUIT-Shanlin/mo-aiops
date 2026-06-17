"""RCA REST API."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.alerts import require_project_id
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.exports.service import ExportStore
from app.models.agent_run import AgentRun
from app.rca.service import RCAService, alert_to_dict
from app.schemas.response import APIError, success


router = APIRouter(tags=["rca"])


class ExecuteFixRequest(BaseModel):
    steps: list[int] = Field(min_length=1)


class ExportRequest(BaseModel):
    format: str = Field(min_length=1)


@router.get("/api/v1/rca/{run_id}")
async def rca_detail(
    run_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        service = RCAService(session, project_id)
        run = await _get_run_or_404(service, run_id)
        data = await service.detail(run)
    return success(data)


@router.get("/api/v1/rca/{run_id}/evidences")
async def rca_evidences(
    run_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        service = RCAService(session, project_id)
        run = await _get_run_or_404(service, run_id)
        data = service.evidences(run)
    return success(data)


@router.get("/api/v1/rca/{run_id}/timeline")
async def rca_timeline(
    run_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        service = RCAService(session, project_id)
        run = await _get_run_or_404(service, run_id)
        data = service.timeline(run)
    return success(data)


@router.get("/api/v1/rca/{run_id}/full-evidence")
async def rca_full_evidence(
    run_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        service = RCAService(session, project_id)
        run = await _get_run_or_404(service, run_id)
        data = service.full_evidence(run)
    return success(data)


@router.get("/api/v1/rca/{run_id}/related-alerts")
async def rca_related_alerts(
    run_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        service = RCAService(session, project_id)
        run = await _get_run_or_404(service, run_id)
        alerts = await service.related_alerts(run)
        data = [alert_to_dict(alert) for alert in alerts]
    return success(data)


@router.post("/api/v1/rca/{run_id}/execute-fix")
async def rca_execute_fix(
    run_id: int,
    payload: ExecuteFixRequest,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        service = RCAService(session, project_id)
        run = await _get_run_or_404(service, run_id)
        try:
            action_ids = await service.execute_fix(run, payload.steps)
        except LookupError as exc:
            raise APIError(ErrorCode.NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            raise APIError(ErrorCode.VALIDATION_ERROR, str(exc)) from exc
        await session.commit()
    return success({"healingActionIds": action_ids})


@router.post("/api/v1/rca/{run_id}/export")
async def export_rca_report(
    run_id: int,
    payload: ExportRequest,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    export_format = payload.format.casefold()
    if export_format not in {"markdown", "pdf"}:
        raise APIError(ErrorCode.VALIDATION_ERROR, "参数校验失败")

    async with request.app.state.sessionmaker() as session:
        service = RCAService(session, project_id)
        run = await _get_run_or_404(service, run_id)
        content = _build_rca_report(run, service.full_evidence(run))

    if export_format == "markdown":
        filename = f"rca-{run_id}.md"
        content_type = "text/markdown; charset=utf-8"
    else:
        filename = f"rca-{run_id}.pdf"
        content_type = "application/pdf"

    artifact = await ExportStore(request.app.state.redis, project_id).save(
        content=content,
        filename=filename,
        content_type=content_type,
    )
    return success({"downloadUrl": artifact.download_url})


async def _get_run_or_404(service: RCAService, run_id: int) -> AgentRun:
    run = await service.get_run(run_id)
    if run is None:
        raise APIError(ErrorCode.NOT_FOUND, "RCA 不存在")
    return run


def _build_rca_report(run: AgentRun, evidence: dict[str, list[Any]]) -> str:
    summary = run.root_cause_summary or run.anomaly_type or "N/A"
    lines = [
        "# RCA Report",
        "",
        "## Summary",
        f"- Run ID: {run.id}",
        f"- Status: {run.status}",
        f"- Trigger Source: {run.trigger_source}",
        f"- Service: {run.fault_service or 'N/A'}",
        f"- Summary: {summary}",
        "",
        "## Evidence",
    ]
    for section in ("metrics", "logs", "traces"):
        lines.append(f"### {section.title()}")
        items = evidence.get(section) or []
        if items:
            lines.extend([f"- {item}" for item in items])
        else:
            lines.append("- None")
        lines.append("")

    lines.append("## Timeline")
    timeline = run.timeline or []
    if timeline:
        for item in timeline:
            event_time = item.get("time", "N/A")
            event = item.get("event", "N/A")
            status = item.get("status")
            if status:
                lines.append(f"- {event_time} | {event} | {status}")
            else:
                lines.append(f"- {event_time} | {event}")
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)
