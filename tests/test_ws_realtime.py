from __future__ import annotations

import json
import time
import asyncio
import gc
import threading
from collections import deque
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings
from app.core.constants import RedisKey
from app.core.projects import ProjectConfig, ProjectsConfig
from app.ws.alerts import _forward_alert_queue
from app.ws.common import forward_pubsub
from app.ws.healing import _forward_action_updates


class RealtimeFakePubSub:
    def __init__(self, redis: "RealtimeFakeRedis"):
        self._redis = redis
        self._channels: set[str] = set()
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self._channels.add(channel)

    async def unsubscribe(self, channel: str) -> None:
        self._channels.discard(channel)

    async def get_message(
        self,
        *,
        ignore_subscribe_messages: bool,
        timeout: float,
    ) -> dict[str, Any] | None:
        message = self._redis.get_next_message(self._channels)
        if message is not None:
            return message
        await asyncio.to_thread(self._redis.wait_for_message, timeout)
        return self._redis.get_next_message(self._channels)

    async def aclose(self) -> None:
        self.closed = True


class RealtimeFakeRedis:
    def __init__(self):
        self._messages: deque[dict[str, Any]] = deque()
        self._lists: dict[str, deque[str]] = {}
        self._lock = threading.Lock()
        self._message_available = threading.Event()
        self._list_available = threading.Event()
        self._pubsub = RealtimeFakePubSub(self)

    def pubsub(self) -> RealtimeFakePubSub:
        return self._pubsub

    def publish(self, channel: str, data: str) -> int:
        with self._lock:
            self._messages.append({"type": "message", "channel": channel, "data": data})
            self._message_available.set()
        return 1

    def lpush(self, key: str, data: str) -> int:
        with self._lock:
            values = self._lists.setdefault(key, deque())
            values.appendleft(data)
            self._list_available.set()
            return len(values)

    async def brpop(self, key: str, timeout: float = 0):
        while True:
            with self._lock:
                values = self._lists.get(key)
                if values:
                    value = values.pop()
                    if not any(self._lists.values()):
                        self._list_available.clear()
                    return (key, value)
                self._list_available.clear()
            if timeout <= 0:
                return None
            if not await asyncio.to_thread(self._list_available.wait, timeout):
                return None

    async def aclose(self) -> None:
        return None

    def get_next_message(self, channels: set[str]) -> dict[str, Any] | None:
        with self._lock:
            for _ in range(len(self._messages)):
                message = self._messages.popleft()
                if message["channel"] in channels:
                    if not self._messages:
                        self._message_available.clear()
                    return message
                self._messages.append(message)
            if not self._messages:
                self._message_available.clear()
        return None

    def wait_for_message(self, timeout: float) -> bool:
        return self._message_available.wait(timeout)


class ClosingFakeWebSocket:
    def __init__(self, redis: Any):
        self.app = SimpleNamespace(state=SimpleNamespace(redis=redis))
        self.close_codes: list[int] = []
        self.sent: list[dict[str, Any]] = []

    async def receive(self) -> dict[str, Any]:
        await asyncio.sleep(60)
        return {"type": "websocket.receive"}

    async def close(self, code: int) -> None:
        self.close_codes.append(code)

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


class ImmediateDisconnectWebSocket(ClosingFakeWebSocket):
    async def receive(self) -> dict[str, Any]:
        return {"type": "websocket.disconnect"}


class SendFailingWebSocket(ClosingFakeWebSocket):
    def __init__(self, redis: Any, exc: Exception):
        super().__init__(redis)
        self._exc = exc

    async def send_json(self, data: dict[str, Any]) -> None:
        raise self._exc


class OneMessageQueueRedis:
    def __init__(self):
        self.calls = 0

    async def brpop(self, key: str, timeout: float = 0):
        self.calls += 1
        return (
            key,
            json.dumps({"event": "alert.new", "data": {"id": "1"}}),
        )


class RaisingQueueRedis:
    async def brpop(self, key: str, timeout: float = 0):
        raise RuntimeError("redis queue failed")


class SubscribeFailingPubSub:
    def __init__(self):
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        raise RuntimeError("subscribe failed")

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def aclose(self) -> None:
        self.closed = True


class MessageFailingPubSub:
    def __init__(self):
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def get_message(
        self,
        *,
        ignore_subscribe_messages: bool,
        timeout: float,
    ) -> dict[str, Any] | None:
        raise RuntimeError("redis read failed")

    async def aclose(self) -> None:
        self.closed = True


class OneMessagePubSub:
    def __init__(self):
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def get_message(
        self,
        *,
        ignore_subscribe_messages: bool,
        timeout: float,
    ) -> dict[str, Any] | None:
        return {
            "type": "message",
            "channel": "aiops:prod:ws:heal",
            "data": json.dumps({"event": "healing.progress", "data": {"id": "10"}}),
        }

    async def aclose(self) -> None:
        self.closed = True


class RaisingMessagePubSub(OneMessagePubSub):
    async def get_message(
        self,
        *,
        ignore_subscribe_messages: bool,
        timeout: float,
    ) -> dict[str, Any] | None:
        raise RuntimeError("redis pubsub failed")


class SinglePubSubRedis:
    def __init__(self, pubsub: Any):
        self._pubsub = pubsub

    def pubsub(self) -> Any:
        return self._pubsub


@pytest.fixture
def ws_projects_config(app_instance):
    app_instance.state.projects_config = ProjectsConfig(
        default_project="prod",
        projects={
            "prod": ProjectConfig(name="Prod", metric_profile="java"),
            "stage": ProjectConfig(name="Stage", metric_profile="java"),
            "disabled": ProjectConfig(
                name="Disabled",
                metric_profile="java",
                enabled=False,
            ),
        },
    )
    return app_instance.state.projects_config


@pytest.fixture
def ws_token():
    settings = get_settings()
    return jwt.encode(
        {"sub": "u1", "role": "admin", "exp": int(time.time()) + 3600},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def test_metrics_ws_rejects_missing_token(app_instance, ws_projects_config):
    client = TestClient(app_instance)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/metrics?project_id=prod"):
            pass

    assert exc.value.code == 1008


def test_metrics_ws_receives_project_scoped_pubsub(
    app_instance,
    ws_projects_config,
    ws_token,
):
    app_instance.state.redis = RealtimeFakeRedis()
    client = TestClient(app_instance)
    redis = app_instance.state.redis
    channel = RedisKey.of("prod", RedisKey.WS_METRICS)
    payload = {
        "event": "metrics.update",
        "data": [{"name": "cpu", "value": 1}],
    }
    with client.websocket_connect(f"/ws/metrics?token={ws_token}&project_id=prod") as ws:
        redis.publish(channel, json.dumps(payload))
        assert ws.receive_json() == payload


def test_notifications_ws_forwards_pubsub_event(
    app_instance,
    ws_projects_config,
    ws_token,
):
    with TestClient(app_instance) as client:
        app_instance.state.redis = RealtimeFakeRedis()
        app_instance.state.projects_config = ws_projects_config
        channel = RedisKey.of("prod", "ws:notifications")
        with client.websocket_connect(
            f"/ws/notifications?token={ws_token}&project_id=prod"
        ) as ws:
            app_instance.state.redis.publish(
                channel,
                json.dumps({"event": "notification.new", "data": {"id": "1"}}),
            )
            assert ws.receive_json() == {
                "event": "notification.new",
                "data": {"id": "1"},
            }


def test_agent_workflow_ws_forwards_pubsub_event(
    app_instance,
    ws_projects_config,
    ws_token,
):
    with TestClient(app_instance) as client:
        app_instance.state.redis = RealtimeFakeRedis()
        app_instance.state.projects_config = ws_projects_config
        channel = RedisKey.of("prod", RedisKey.WS_AGENT)
        with client.websocket_connect(
            f"/ws/agent/workflow?token={ws_token}&project_id=prod"
        ) as ws:
            app_instance.state.redis.publish(
                channel,
                json.dumps({"event": "workflow.step.updated", "data": {"id": "7"}}),
            )
            assert ws.receive_json()["event"] == "workflow.step.updated"


def test_healing_action_ws_filters_action_id(
    app_instance,
    ws_projects_config,
    ws_token,
):
    with TestClient(app_instance) as client:
        app_instance.state.redis = RealtimeFakeRedis()
        app_instance.state.projects_config = ws_projects_config
        channel = RedisKey.of("prod", RedisKey.WS_HEAL)
        with client.websocket_connect(
            f"/ws/healing/actions/10?token={ws_token}&project_id=prod"
        ) as ws:
            app_instance.state.redis.publish(
                channel,
                json.dumps({"event": "healing.progress", "data": {"id": "9"}}),
            )
            app_instance.state.redis.publish(
                channel,
                json.dumps({"event": "healing.progress", "data": {"id": "10"}}),
            )
            assert ws.receive_json()["data"]["id"] == "10"


def test_alerts_ws_brpop_receives_project_queue(
    app_instance,
    ws_projects_config,
    ws_token,
):
    with TestClient(app_instance) as client:
        app_instance.state.redis = RealtimeFakeRedis()
        app_instance.state.projects_config = ws_projects_config
        key = RedisKey.of("prod", RedisKey.ALERTS)
        with client.websocket_connect(f"/ws/alerts?token={ws_token}&project_id=prod") as ws:
            app_instance.state.redis.lpush(
                key,
                json.dumps({"event": "alert.new", "data": {"id": "1"}}),
            )
            assert ws.receive_json()["event"] == "alert.new"


@pytest.mark.asyncio
async def test_forward_pubsub_closes_pubsub_when_subscribe_fails():
    pubsub = SubscribeFailingPubSub()
    websocket = ClosingFakeWebSocket(SinglePubSubRedis(pubsub))

    await forward_pubsub(websocket, "aiops:prod:ws:metrics")

    assert pubsub.closed is True
    assert pubsub.unsubscribed == []
    assert websocket.close_codes == [1011]


@pytest.mark.asyncio
async def test_forward_pubsub_closes_ws_1011_and_cleans_up_when_get_message_fails():
    pubsub = MessageFailingPubSub()
    websocket = ClosingFakeWebSocket(SinglePubSubRedis(pubsub))

    await forward_pubsub(websocket, "aiops:prod:ws:metrics")

    assert websocket.close_codes == [1011]
    assert pubsub.subscribed == ["aiops:prod:ws:metrics"]
    assert pubsub.unsubscribed == ["aiops:prod:ws:metrics"]
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_alerts_loop_drains_redis_task_exception_when_disconnect_races():
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, Any]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    websocket = ImmediateDisconnectWebSocket(RaisingQueueRedis())

    try:
        await _forward_alert_queue(websocket, "aiops:prod:alerts")
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert contexts == []


@pytest.mark.asyncio
async def test_healing_loop_drains_redis_task_exception_when_disconnect_races():
    loop = asyncio.get_running_loop()
    contexts: list[dict[str, Any]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    pubsub = RaisingMessagePubSub()
    websocket = ImmediateDisconnectWebSocket(SinglePubSubRedis(pubsub))

    try:
        await _forward_action_updates(websocket, "aiops:prod:ws:heal", "10")
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert contexts == []
    assert pubsub.unsubscribed == ["aiops:prod:ws:heal"]
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_alerts_loop_exits_when_send_json_runtime_error():
    redis = OneMessageQueueRedis()
    websocket = SendFailingWebSocket(redis, RuntimeError("client disconnected"))

    await _forward_alert_queue(websocket, "aiops:prod:alerts")

    assert redis.calls == 1


@pytest.mark.asyncio
async def test_healing_loop_exits_and_cleans_up_when_send_json_os_error():
    pubsub = OneMessagePubSub()
    websocket = SendFailingWebSocket(
        SinglePubSubRedis(pubsub),
        OSError("client disconnected"),
    )

    await _forward_action_updates(websocket, "aiops:prod:ws:heal", "10")

    assert pubsub.unsubscribed == ["aiops:prod:ws:heal"]
    assert pubsub.closed is True
