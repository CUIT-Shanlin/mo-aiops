from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.api.alerts import require_project_id
from app.core.constants import ErrorCode
from app.core.security import CurrentUser, get_current_user
from app.exports.service import ExportStore
from app.schemas.response import APIError


router = APIRouter(tags=["exports"])
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_EXTENSION_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


def _content_disposition(filename: str) -> str:
    safe_filename = _sanitize_filename(filename)
    return f'attachment; filename="{safe_filename}"'


def _sanitize_filename(filename: str) -> str:
    cleaned = (
        filename.replace("\r", "")
        .replace("\n", "")
        .replace('"', "")
        .replace("\\", "")
    )
    if cleaned and _SAFE_FILENAME_RE.fullmatch(cleaned):
        return cleaned

    suffix = ""
    if "." in cleaned:
        candidate = f".{cleaned.rsplit('.', 1)[-1]}"
        if _SAFE_EXTENSION_RE.fullmatch(candidate):
            suffix = candidate.lower()
    return f"export{suffix or '.bin'}"


@router.get("/api/v1/exports/{export_id}/download")
async def download_export(
    export_id: str,
    request: Request,
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
    project_id: Annotated[str, Depends(require_project_id)],
) -> Response:
    artifact = await ExportStore(request.app.state.redis, project_id).get(export_id)
    if artifact is None:
        raise APIError(ErrorCode.NOT_FOUND, "导出文件不存在")
    return Response(
        content=artifact.content,
        media_type=artifact.content_type,
        headers={
            "Content-Disposition": _content_disposition(artifact.filename),
        },
    )
