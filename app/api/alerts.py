"""Alerts REST API."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.constants import RedisKey
from app.alerts.service import AlertService
from app.alerts.state_machine import InvalidAlertTransition
from app.audit.service import AuditService
from app.core.constants import ErrorCode
from app.core.projects import get_projects_config
from app.core.security import CurrentUser, get_current_user
from app.logs.service import LogFilters, LogsService
from app.models.alert_event import AlertEvent
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.alerts import AlertEventRepository
from app.repositories.audit import AuditLogRepository
from app.schemas.response import APIError, success


router = APIRouter(tags=["alerts"])

_SEVERITY_RANK = {
    "info": 0,
    "warning": 1,
    "critical": 2,
}

_AUDIT_ACTION_BY_TARGET_STATUS = {
    "acknowledged": "ALERT_ACKNOWLEDGE",
    "suppressed": "ALERT_SUPPRESS",
    "resolved": "ALERT_RESOLVE",
}


class TransitionRequest(BaseModel):
    reason: str | None = None


class SuppressRequest(BaseModel):
    duration: int = Field(ge=0)
    reason: str | None = None


class BatchSuppressRequest(BaseModel):
    ids: list[int | str] = Field(min_length=1)
    duration: int = Field(ge=0)
    reason: str | None = None

    def normalized_ids(self) -> list[int]:
        alert_ids: list[int] = []
        for raw_id in self.ids:
            try:
                alert_ids.append(int(raw_id))
            except (TypeError, ValueError) as exc:
                raise APIError(ErrorCode.VALIDATION_ERROR, "告警 ID 格式无效") from exc
        return alert_ids


class AlertWebhookPayload(BaseModel):
    alerts: list[dict[str, Any]] = Field(default_factory=list)


async def require_project_id(
    request: Request,
    x_project_id: Annotated[str | None, Header()] = None,
) -> str:
    """Resolve X-Project-Id; missing, unknown, and disabled all look not found."""
    if x_project_id:
        config = getattr(request.app.state, "projects_config", None) or get_projects_config()
        project = config.projects.get(x_project_id)
        if project is not None and project.enabled:
            return x_project_id
    raise APIError(ErrorCode.NOT_FOUND, "项目不存在")


async def _resolve_project_id_for_webhook(
    request: Request,
    x_project_id: Annotated[str | None, Header()] = None,
) -> str:
    """Resolve project for webhook: header first, then alert labels fallback.

    When an explicit ``X-Project-Id`` header is present, it is authoritative —
    an invalid or disabled project is rejected immediately without falling back
    to labels or the default project.  This prevents silent misrouting when the
    caller intentionally targets a specific project.
    """
    config = getattr(request.app.state, "projects_config", None) or get_projects_config()

    if x_project_id is not None:
        project = config.projects.get(x_project_id)
        if project is not None and project.enabled:
            return x_project_id
        raise APIError(ErrorCode.NOT_FOUND, "项目不存在")

    import json as _json
    body = await request.body()
    try:
        payload = _json.loads(body)
    except Exception:
        payload = {}
    for alert in payload.get("alerts") or []:
        labels = alert.get("labels") or {}
        pid = labels.get("project_id")
        if isinstance(pid, str) and pid:
            project = config.projects.get(pid)
            if project is not None and project.enabled:
                return pid

    default = config.default_project
    project = config.projects.get(default)
    if project is not None and project.enabled:
        return default

    raise APIError(ErrorCode.NOT_FOUND, "项目不存在")


@router.post("/api/v1/alerts/webhook")
async def alert_webhook(
    payload: AlertWebhookPayload,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(_resolve_project_id_for_webhook)],
) -> JSONResponse:
    raw_payload = payload.model_dump_json()
    await request.app.state.redis.lpush(RedisKey.of(project_id, RedisKey.INGEST), raw_payload)
    return JSONResponse(
        status_code=202,
        content={"code": 0, "message": "success", "data": {"accepted": True}},
    )


@router.get("/api/v1/alerts/stats")
async def alert_stats(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        data = await AlertEventRepository(session, project_id).stats()
    return success(data)


@router.get("/api/v1/alerts")
async def list_alerts(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    service: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        result = await AlertEventRepository(session, project_id).search(
            status=status,
            severity=severity,
            service=service,
            namespace=namespace,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    return success(
        {
            "total": result.total,
            "page": result.page,
            "pageSize": result.size,
            "items": [_alert_to_dict(alert) for alert in result.items],
        }
    )


@router.get("/api/v1/alerts/{alert_id}/rca-id")
async def get_alert_rca_id(
    alert_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        alert = await AlertEventRepository(session, project_id).get(alert_id)
        if alert is None:
            raise APIError(ErrorCode.NOT_FOUND, "告警不存在")
        if alert.agent_run_id is None:
            return success({"rcaId": None, "summary": None, "confidenceScore": 0})

        run = await AgentRunRepository(session, project_id).get(alert.agent_run_id)
        if run is None:
            return success(
                {
                    "rcaId": alert.agent_run_id,
                    "summary": None,
                    "confidenceScore": 0,
                }
            )
    return success(
        {
            "rcaId": run.id,
            "summary": run.root_cause_summary,
            "confidenceScore": run.confidence or 0,
        }
    )


@router.get("/api/v1/alerts/{alert_id}/logs")
async def get_alert_logs(
    alert_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200, alias="pageSize"),
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        alert = await AlertEventRepository(session, project_id).get(alert_id)
    if alert is None:
        raise APIError(ErrorCode.NOT_FOUND, "告警不存在")

    service = alert.service or _optional_label(alert, "service")
    namespace = alert.namespace or _optional_label(alert, "namespace")
    data = await LogsService(request.app.state.redis).search(
        project_id,
        LogFilters(service=service),
        page=1,
        page_size=10000,
    )
    items = data["items"]
    if namespace:
        items = [item for item in items if item.get("namespace") == namespace]

    offset = (page - 1) * page_size
    counts = _log_level_counts(items)
    return success(
        {
            "total": len(items),
            **counts,
            "page": page,
            "pageSize": page_size,
            "items": items[offset : offset + page_size],
        }
    )


@router.get("/api/v1/alerts/{alert_id}")
async def get_alert(
    alert_id: int,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        alert = await AlertEventRepository(session, project_id).get(alert_id)
    if alert is None:
        raise APIError(ErrorCode.NOT_FOUND, "告警不存在")
    return success(_alert_to_dict(alert))


@router.put("/api/v1/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: int,
    payload: TransitionRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    return await _transition_alert(
        request=request,
        project_id=project_id,
        current_user=current_user,
        alert_id=alert_id,
        target_status="acknowledged",
        reason=payload.reason,
    )


@router.put("/api/v1/alerts/{alert_id}/suppress")
async def suppress_alert(
    alert_id: int,
    payload: SuppressRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    return await _transition_alert(
        request=request,
        project_id=project_id,
        current_user=current_user,
        alert_id=alert_id,
        target_status="suppressed",
        reason=payload.reason,
        suppress_duration=payload.duration,
    )


@router.put("/api/v1/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    payload: TransitionRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    return await _transition_alert(
        request=request,
        project_id=project_id,
        current_user=current_user,
        alert_id=alert_id,
        target_status="resolved",
        reason=payload.reason,
    )


@router.post("/api/v1/alerts/batch-suppress")
async def batch_suppress_alerts(
    payload: BatchSuppressRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    alert_ids = payload.normalized_ids()
    async with request.app.state.sessionmaker() as session:
        alert_repository = AlertEventRepository(session, project_id)
        service = AlertService(alert_repository)
        audit_service = AuditService(AuditLogRepository(session, project_id))
        for alert_id in alert_ids:
            before = await alert_repository.get(alert_id)
            if before is None:
                raise APIError(ErrorCode.NOT_FOUND, "告警不存在")
            before_status = before.status
            try:
                alert = await service.transition(alert_id, "suppressed", reason=payload.reason)
            except LookupError as exc:
                raise APIError(ErrorCode.NOT_FOUND, "告警不存在") from exc
            except InvalidAlertTransition as exc:
                raise APIError(ErrorCode.VALIDATION_ERROR, "告警状态转换无效") from exc
            await _record_transition_audit(
                audit_service=audit_service,
                current_user=current_user,
                request=request,
                alert_id=alert.id,
                before_status=before_status,
                target_status="suppressed",
                reason=payload.reason,
                suppress_duration=payload.duration,
            )
        await session.commit()
    return success({"success": True, "count": len(alert_ids)})


@router.get("/api/v1/alert-groups")
async def alert_groups(
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        alerts = await AlertEventRepository(session, project_id).list_active_for_grouping()
    return success(_build_groups(alerts))


async def _transition_alert(
    *,
    request: Request,
    project_id: str,
    current_user: CurrentUser,
    alert_id: int,
    target_status: str,
    reason: str | None,
    suppress_duration: int | None = None,
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        alert_repository = AlertEventRepository(session, project_id)
        before = await alert_repository.get(alert_id)
        if before is None:
            raise APIError(ErrorCode.NOT_FOUND, "告警不存在")
        before_status = before.status
        service = AlertService(alert_repository)
        audit_service = AuditService(AuditLogRepository(session, project_id))
        try:
            alert = await service.transition(alert_id, target_status, reason=reason)
        except LookupError as exc:
            raise APIError(ErrorCode.NOT_FOUND, "告警不存在") from exc
        except InvalidAlertTransition as exc:
            raise APIError(ErrorCode.VALIDATION_ERROR, "告警状态转换无效") from exc
        await _record_transition_audit(
            audit_service=audit_service,
            current_user=current_user,
            request=request,
            alert_id=alert.id,
            before_status=before_status,
            target_status=target_status,
            reason=reason,
            suppress_duration=suppress_duration,
        )
        await session.commit()
    return success({"success": True})


async def _record_transition_audit(
    *,
    audit_service: AuditService,
    current_user: CurrentUser,
    request: Request,
    alert_id: int,
    before_status: str,
    target_status: str,
    reason: str | None,
    suppress_duration: int | None = None,
) -> None:
    after_state: dict[str, Any] = {"status": target_status}
    if target_status == "suppressed" and suppress_duration is not None:
        after_state["duration"] = suppress_duration

    await audit_service.record(
        operator_type="user",
        operator_name=current_user.user_id or current_user.role,
        operator_uid=current_user.user_id,
        action=_AUDIT_ACTION_BY_TARGET_STATUS.get(
            target_status,
            "ALERT_STATUS_TRANSITION",
        ),
        resource_type="alert",
        resource_id=str(alert_id),
        before_state={"status": before_status},
        after_state=after_state,
        ip_address=_client_host(request),
        reason=reason,
        result="success",
    )


def _alert_to_dict(alert: AlertEvent) -> dict[str, Any]:
    return {
        "id": alert.id,
        "name": alert.name,
        "severity": alert.severity,
        "status": alert.status,
        "service": alert.service,
        "namespace": alert.namespace,
        "pod": alert.pod,
        "fingerprint": alert.fingerprint,
        "labels": alert.labels,
        "annotations": alert.annotations,
        "firedAt": _format_datetime(alert.fired_at),
        "lastSeenAt": _format_datetime(alert.last_seen_at),
        "resolvedAt": _format_datetime(alert.resolved_at),
        "duration": _format_duration(alert),
        "alertCount": alert.alert_count,
        "relatedRCA": alert.agent_run_id,
        "relatedHealing": alert.related_heal_action_id,
    }


def _optional_label(alert: AlertEvent, key: str) -> str | None:
    value = alert.labels.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _log_level_counts(logs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"errorCount": 0, "warnCount": 0, "infoCount": 0, "debugCount": 0}
    for log in logs:
        level = str(log.get("level", "")).casefold()
        if level == "error":
            counts["errorCount"] += 1
        elif level in {"warn", "warning"}:
            counts["warnCount"] += 1
        elif level == "info":
            counts["infoCount"] += 1
        elif level == "debug":
            counts["debugCount"] += 1
    return counts


def _build_groups(alerts: list[AlertEvent]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[AlertEvent]] = defaultdict(list)
    for alert in alerts:
        service = alert.service or str(alert.labels.get("service") or "unknown")
        name = alert.name or str(alert.labels.get("alertname") or "unknown")
        grouped[(service, name)].append(alert)

    groups: list[dict[str, Any]] = []
    for (service, name), rows in grouped.items():
        sorted_rows = sorted(rows, key=lambda item: (item.fired_at, item.id))
        groups.append(
            {
                "id": f"{service}:{name}",
                "service": service,
                "name": name,
                "severity": _max_severity(sorted_rows),
                "status": "active",
                "alertCount": len(sorted_rows),
                "alertIds": [alert.id for alert in sorted_rows],
                "firstFiredAt": _format_datetime(sorted_rows[0].fired_at),
                "lastFiredAt": _format_datetime(sorted_rows[-1].fired_at),
            }
        )
    return sorted(groups, key=lambda item: (item["service"], item["name"]))


def _max_severity(alerts: list[AlertEvent]) -> str:
    return max(alerts, key=lambda alert: _SEVERITY_RANK.get(alert.severity, -1)).severity


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _format_duration(alert: AlertEvent) -> str:
    end = alert.resolved_at or alert.last_seen_at or alert.fired_at
    minutes = max(int((end - alert.fired_at).total_seconds() // 60), 0)
    if minutes < 60:
        return f"{minutes}分钟"
    hours, remainder = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}小时{remainder}分钟" if remainder else f"{hours}小时"
    days, remainder_hours = divmod(hours, 24)
    return f"{days}天{remainder_hours}小时" if remainder_hours else f"{days}天"


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client is not None else None
