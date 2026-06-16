from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.security import CurrentUser, get_current_user
from app.schemas.response import success


router = APIRouter(prefix="/api/v1", tags=["projects"])


@router.get("/projects")
async def list_projects(
    request: Request,
    _current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    config = request.app.state.projects_config
    items = [
        {
            "id": project_id,
            "name": project.name,
            "metricProfile": project.metric_profile,
        }
        for project_id, project in config.projects.items()
        if project.enabled
    ]
    return success(items)
