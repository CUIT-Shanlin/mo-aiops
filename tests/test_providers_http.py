from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.projects import (
    LokiAuthType,
    LokiDatasourceConfig,
    PrometheusDatasourceConfig,
    TempoDatasourceConfig,
)
from app.providers.base import ProviderError
from app.providers.loki import LokiProvider
from app.providers.prometheus import PrometheusProvider
from app.providers.tempo import TempoProvider


@dataclass
class FakeResponse:
    status: int = 200
    payload: object | None = None
    text_value: str = "ok"

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def json(self) -> object:
        return self.payload

    async def text(self) -> str:
        return self.text_value


class FakeSession:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse()
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_prometheus_instant_query_uses_api_and_query_param():
    session = FakeSession(FakeResponse(payload={"status": "success"}))
    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(base_url="http://prom:9090"),
        session=session,
    )

    await provider.query(query_type="instant", query="up")

    assert session.calls == [
        ("http://prom:9090/api/v1/query", {"params": {"query": "up"}, "ssl": True})
    ]


@pytest.mark.asyncio
async def test_prometheus_range_query_uses_range_endpoint_and_time_params():
    session = FakeSession(FakeResponse(payload={"status": "success"}))
    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(base_url="http://prom:9090"),
        session=session,
    )

    await provider.query(
        query_type="range",
        query="up",
        start="2026-06-16T00:00:00Z",
        end="2026-06-16T01:00:00Z",
        step="30s",
    )

    assert session.calls == [
        (
            "http://prom:9090/api/v1/query_range",
            {
                "params": {
                    "query": "up",
                    "start": "2026-06-16T00:00:00Z",
                    "end": "2026-06-16T01:00:00Z",
                    "step": "30s",
                },
                "ssl": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_loki_x_scope_orgid_sets_header():
    session = FakeSession(FakeResponse(payload={"data": []}))
    provider = LokiProvider(
        project_id="proj-a",
        datasource_type="loki",
        config=LokiDatasourceConfig(
            base_url="http://loki:3100",
            auth_type=LokiAuthType.X_SCOPE_ORG_ID,
            tenant_id="tenant-1",
        ),
        session=session,
    )

    await provider.query(query='{job="app"}')

    assert session.calls == [
        (
            "http://loki:3100/loki/api/v1/query_range",
            {
                "params": {
                    "query": '{job="app"}',
                    "limit": 100,
                    "direction": "backward",
                },
                "headers": {"X-Scope-OrgID": "tenant-1"},
                "ssl": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_loki_basic_auth_is_used_when_credentials_exist():
    session = FakeSession(FakeResponse(payload={"data": []}))
    provider = LokiProvider(
        project_id="proj-a",
        datasource_type="loki",
        config=LokiDatasourceConfig(
            base_url="http://loki:3100",
            auth_type=LokiAuthType.BASIC,
            username="alice",
            password="secret",
        ),
        session=session,
    )

    await provider.query(query="sum(rate({job='app'}[5m]))")

    auth = session.calls[0][1]["auth"]
    assert auth is not None


@pytest.mark.asyncio
async def test_tempo_trace_query_uses_trace_endpoint():
    session = FakeSession(FakeResponse(payload={"traceID": "abc"}))
    provider = TempoProvider(
        project_id="proj-a",
        datasource_type="tempo",
        config=TempoDatasourceConfig(base_url="http://tempo:3200"),
        session=session,
    )

    await provider.query(query_type="trace", trace_id="abc")

    assert session.calls == [("http://tempo:3200/api/traces/abc", {"ssl": True})]


@pytest.mark.asyncio
async def test_tempo_search_query_filters_none_params():
    session = FakeSession(FakeResponse(payload={"data": []}))
    provider = TempoProvider(
        project_id="proj-a",
        datasource_type="tempo",
        config=TempoDatasourceConfig(base_url="http://tempo:3200"),
        session=session,
    )

    await provider.query(
        query_type="search",
        service_name="checkout",
        start="2026-06-16T00:00:00Z",
        end=None,
        limit=None,
    )

    assert session.calls == [
        (
            "http://tempo:3200/api/search",
            {
                "params": {
                    "service_name": "checkout",
                    "start": "2026-06-16T00:00:00Z",
                },
                "ssl": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_http_2xx_failure_raises_provider_error():
    session = FakeSession(FakeResponse(status=500, text_value="internal error"))
    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(base_url="http://prom:9090"),
        session=session,
    )

    with pytest.raises(ProviderError, match="HTTP 500"):
        await provider.query(query_type="instant", query="up")


@pytest.mark.asyncio
async def test_validate_scopes_success_returns_connectivity_true():
    session = FakeSession(FakeResponse(payload={"status": "ok"}, text_value="ready"))
    provider = TempoProvider(
        project_id="proj-a",
        datasource_type="tempo",
        config=TempoDatasourceConfig(base_url="http://tempo:3200"),
        session=session,
    )

    result = await provider.validate_scopes()

    assert result == {"connectivity": True}


@pytest.mark.asyncio
async def test_close_does_not_close_injected_session():
    session = FakeSession(FakeResponse(payload={"status": "success"}))
    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(base_url="http://prom:9090"),
        session=session,
    )

    await provider.close()

    assert session.closed is False
