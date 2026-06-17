from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, MutableMapping

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.constants import RedisKey
from app.ws.common import (
    authenticate_ws,
    message_data,
    projects_config,
    resolve_ws_project_id,
)

router = APIRouter()


@router.websocket("/ws/healing/actions/{action_id}")
async def healing_action_ws(websocket: WebSocket, action_id: str) -> None:
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
    await _forward_action_updates(
        websocket,
        RedisKey.of(project_id, RedisKey.WS_HEAL),
        action_id,
    )


async def _forward_action_updates(
    websocket: WebSocket,
    channel: str,
    action_id: str,
) -> None:
    pubsub = websocket.app.state.redis.pubsub()
    subscribed = False
    client_task: asyncio.Task[MutableMapping[str, Any]] | None = None
    try:
        try:
            await pubsub.subscribe(channel)
            subscribed = True
        except Exception:
            with suppress(Exception):
                await websocket.close(code=1011)
            return

        client_task = asyncio.create_task(websocket.receive())
        while True:
            redis_task = asyncio.create_task(
                pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            )
            done, _pending = await asyncio.wait(
                {redis_task, client_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if redis_task not in done:
                redis_task.cancel()
                with suppress(asyncio.CancelledError):
                    await redis_task

            message: dict[str, Any] | None = None
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
                if message is None or message.get("type") != "message":
                    continue
                data = message_data(message)
                if data is None:
                    continue
                payload_data = data.get("data")
                if isinstance(payload_data, dict) and str(payload_data.get("id")) == action_id:
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
        if subscribed:
            with suppress(Exception):
                await pubsub.unsubscribe(channel)
        with suppress(Exception):
            await pubsub.aclose()
