from __future__ import annotations

import asyncio
import json
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings


class FakeEngine:
    async def dispose(self) -> None:
        return None


class FakePubSub:
    def __init__(self, messages: list[dict]):
        self.messages = messages
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.get_message_calls = 0
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
    ):
        await asyncio.sleep(0)
        self.get_message_calls += 1
        if self.messages:
            return self.messages.pop(0)
        return None

    async def aclose(self) -> None:
        self.closed = True


class FakeRedis:
    def __init__(self, pubsub: FakePubSub):
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self) -> FakePubSub:
        return self._pubsub

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def metrics_ws_app(monkeypatch, tmp_path):
    from app.main import get_app

    path = tmp_path / "projects.yaml"
    path.write_text(
        """
default_project: demo
projects:
  demo:
    name: Demo
    metric_profile: java
  stage:
    name: Stage
    metric_profile: java
  disabled:
    name: Disabled
    metric_profile: java
    enabled: false
""".strip()
    )
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///tmp/test.db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(path))
    monkeypatch.setenv("STARTUP_PROVIDER_VALIDATION", "false")
    monkeypatch.setenv("STARTUP_COLLECTOR_ENABLED", "false")
    get_settings.cache_clear()

    pubsub = FakePubSub([])
    redis = FakeRedis(pubsub)

    monkeypatch.setattr("app.main.run_upgrade_head", lambda: None)
    monkeypatch.setattr("app.main.ping_db", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.ping_redis", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr("app.main.create_engine", lambda *_args, **_kwargs: FakeEngine())
    monkeypatch.setattr("app.main.make_sessionmaker", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("app.main.create_redis", lambda *_args, **_kwargs: redis)

    return get_app(), pubsub


def _token(role: str = "admin", exp_delta: int = 3600) -> str:
    payload = {"sub": "u1", "role": role, "exp": int(time.time()) + exp_delta}
    return jwt.encode(payload, "secret", algorithm="HS256")


def test_metrics_ws_requires_query_token(metrics_ws_app):
    app, _pubsub = metrics_ws_app

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/metrics"):
                pass

    assert exc.value.code == 1008


def test_metrics_ws_rejects_unknown_project(metrics_ws_app):
    app, _pubsub = metrics_ws_app

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws/metrics?token={_token()}&project_id=missing"):
                pass

    assert exc.value.code == 1008


def test_metrics_ws_rejects_disabled_project(metrics_ws_app):
    app, _pubsub = metrics_ws_app

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws/metrics?token={_token()}&project_id=disabled"):
                pass

    assert exc.value.code == 1008


def test_metrics_ws_requires_project_id(metrics_ws_app):
    app, pubsub = metrics_ws_app

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws/metrics?token={_token()}"):
                pass

    assert exc.value.code == 1008
    assert pubsub.subscribed == []


def test_metrics_ws_rejects_empty_project_id_query(metrics_ws_app):
    app, pubsub = metrics_ws_app

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws/metrics?token={_token()}&project_id="):
                pass

    assert exc.value.code == 1008
    assert pubsub.subscribed == []


def test_metrics_ws_subscribes_explicit_project_and_forwards_json(metrics_ws_app):
    app, pubsub = metrics_ws_app
    pubsub.messages.append(
        {
            "type": "message",
            "channel": "aiops:demo:ws:metrics",
            "data": json.dumps(
                {
                    "type": "metrics.snapshot",
                    "projectId": "demo",
                    "metrics": [{"canonicalName": "conn.active", "value": 3.0}],
                }
            ),
        }
    )

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/metrics?token={_token()}&project_id=demo") as websocket:
            assert websocket.receive_json() == {
                "type": "metrics.snapshot",
                "projectId": "demo",
                "metrics": [{"canonicalName": "conn.active", "value": 3.0}],
            }

    assert pubsub.subscribed == ["aiops:demo:ws:metrics"]
    assert pubsub.unsubscribed == ["aiops:demo:ws:metrics"]
    assert pubsub.closed is True


def test_metrics_ws_client_disconnect_cleans_up_without_redis_messages(metrics_ws_app):
    app, pubsub = metrics_ws_app

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/metrics?token={_token()}&project_id=demo") as websocket:
            websocket.send_json({"type": "client.noop"})
            pass

    assert pubsub.subscribed == ["aiops:demo:ws:metrics"]
    assert pubsub.unsubscribed == ["aiops:demo:ws:metrics"]
    assert pubsub.get_message_calls >= 1
    assert pubsub.closed is True


def test_metrics_ws_skips_bad_payloads_and_forwards_next_good_message(metrics_ws_app):
    app, pubsub = metrics_ws_app
    pubsub.messages.extend(
        [
            {"type": "message", "channel": "aiops:demo:ws:metrics", "data": "not-json"},
            {"type": "message", "channel": "aiops:demo:ws:metrics", "data": b"\xff"},
            {
                "type": "message",
                "channel": "aiops:demo:ws:metrics",
                "data": json.dumps([1]),
            },
            {
                "type": "message",
                "channel": "aiops:demo:ws:metrics",
                "data": json.dumps(None),
            },
            {"type": "message", "channel": "aiops:demo:ws:metrics", "data": 1},
            {"type": "message", "channel": "aiops:demo:ws:metrics", "data": None},
            {
                "type": "message",
                "channel": "aiops:demo:ws:metrics",
                "data": json.dumps({"type": "metrics.snapshot", "projectId": "demo"}),
            },
        ]
    )

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/metrics?token={_token()}&project_id=demo") as websocket:
            assert websocket.receive_json() == {
                "type": "metrics.snapshot",
                "projectId": "demo",
            }

    assert pubsub.unsubscribed == ["aiops:demo:ws:metrics"]
    assert pubsub.closed is True


def test_metrics_ws_rejects_invalid_token(metrics_ws_app):
    app, _pubsub = metrics_ws_app

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/metrics?token=not-a-token"):
                pass

    assert exc.value.code == 1008


def test_metrics_ws_closes_1011_when_project_config_load_fails(metrics_ws_app, monkeypatch):
    app, _pubsub = metrics_ws_app

    def _raise_config_error():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("app.ws.common.get_projects_config", _raise_config_error)

    with TestClient(app) as client:
        delattr(app.state, "projects_config")
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws/metrics?token={_token()}&project_id=demo"):
                pass

    assert exc.value.code == 1011
