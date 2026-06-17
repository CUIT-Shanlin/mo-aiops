from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from json import JSONDecodeError
from typing import Any, MutableMapping

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.constants import RedisKey
from app.ws.common import authenticate_ws, projects_config, resolve_ws_project_id

router = APIRouter()


@router.websocket("/ws/alerts")
async def alerts_ws(websocket: WebSocket) -> None:
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
    await _forward_alert_queue(websocket, RedisKey.of(project_id, RedisKey.ALERTS))


async def _forward_alert_queue(websocket: WebSocket, key: str) -> None:
    redis = websocket.app.state.redis
    client_task: asyncio.Task[MutableMapping[str, Any]] | None = None
    try:
        client_task = asyncio.create_task(websocket.receive())
        while True:
            redis_task = asyncio.create_task(redis.brpop(key, timeout=0.5))
            done, _pending = await asyncio.wait(
                {redis_task, client_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if redis_task not in done:
                redis_task.cancel()
                with suppress(asyncio.CancelledError):
                    await redis_task

            message: Any = None
            redis_failed = False
            if redis_task in done:
                try:
                    message = redis_task.result()
                except Exception:
                    redis_failed = True

            if client_task in done:
                try:
                    client_message = client_task.result()
                except WebSocketDisconnect:
                    break
                if client_message.get("type") == "websocket.disconnect":
                    break
                client_task = asyncio.create_task(websocket.receive())

            if redis_failed:
                with suppress(Exception):
                    await websocket.close(code=1011)
                break

            if redis_task in done:
                data = _brpop_data(message)
                if data is not None:
                    try:
                        await websocket.send_json(data)
                    except (WebSocketDisconnect, RuntimeError, OSError):
                        break
    except WebSocketDisconnect:
        pass
    finally:
        if client_task is not None and not client_task.done():
            client_task.cancel()
            with suppress(asyncio.CancelledError):
                await client_task


def _brpop_data(message: Any) -> dict[str, Any] | None:
    if message is None:
        return None
    value = message[1] if isinstance(message, (list, tuple)) and len(message) >= 2 else message
    try:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if isinstance(value, str):
            value = json.loads(value)
    except (JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None
