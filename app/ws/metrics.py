from __future__ import annotations

from fastapi import APIRouter, WebSocket

from app.core.constants import RedisKey
from app.ws.common import (
    authenticate_ws,
    forward_pubsub,
    projects_config,
    resolve_ws_project_id,
)

router = APIRouter()


@router.websocket("/ws/metrics")
async def metrics_ws(websocket: WebSocket) -> None:
    if not await authenticate_ws(websocket):
        return

    try:
        config = projects_config(websocket)
    except Exception:
        await websocket.close(code=1011)
        return

    project_id = resolve_ws_project_id(websocket, config)
    if project_id is None:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    channel = RedisKey.of(project_id, RedisKey.WS_METRICS)
    await forward_pubsub(websocket, channel)
