from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from json import JSONDecodeError
from typing import Any, MutableMapping

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings
from app.core.projects import ProjectsConfig, get_projects_config
from app.core.security import decode_and_verify
from app.schemas.response import APIError


def projects_config(websocket: WebSocket) -> ProjectsConfig:
    config = getattr(websocket.app.state, "projects_config", None)
    return config if config is not None else get_projects_config()


def resolve_ws_project_id(websocket: WebSocket, config: ProjectsConfig) -> str | None:
    project_id = websocket.query_params.get("project_id")
    if not project_id:
        return None
    project = config.projects.get(project_id)
    if project is None or not project.enabled:
        return None
    return project_id


async def authenticate_ws(websocket: WebSocket) -> bool:
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return False
    settings = get_settings()
    try:
        decode_and_verify(token, settings.jwt_secret, settings.jwt_algorithm)
    except APIError:
        await websocket.close(code=1008)
        return False
    return True


def message_data(message: dict[str, Any]) -> dict[str, Any] | None:
    data = message.get("data")
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        if isinstance(data, str):
            data = json.loads(data)
    except (JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


async def forward_pubsub(websocket: WebSocket, channel: str) -> None:
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
            done, pending = await asyncio.wait(
                {redis_task, client_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if redis_task not in done:
                redis_task.cancel()
                with suppress(asyncio.CancelledError):
                    await redis_task

            if client_task in done:
                try:
                    client_message = client_task.result()
                except WebSocketDisconnect:
                    break
                if client_message.get("type") == "websocket.disconnect":
                    break
                client_task = asyncio.create_task(websocket.receive())

            if redis_task in done:
                try:
                    message = redis_task.result()
                except Exception:
                    with suppress(Exception):
                        await websocket.close(code=1011)
                    break
                if message is None or message.get("type") != "message":
                    continue
                data = message_data(message)
                if data is not None:
                    await websocket.send_json(data)
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
