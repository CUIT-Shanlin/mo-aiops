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


class SequentialResponseSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)

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
async def test_prometheus_basic_auth_is_used_when_credentials_exist():
    session = FakeSession(FakeResponse(payload={"status": "success"}))
    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(
            base_url="http://prom:9090",
            username="alice",
            password="secret",
        ),
        session=session,
    )

    await provider.query(query_type="instant", query="up")

    request_kwargs = session.calls[0][1]
    assert "auth" not in request_kwargs
    assert request_kwargs["headers"]["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_prometheus_validate_scopes_hits_healthy_endpoint():
    session = FakeSession(FakeResponse(status=200, text_value="ok"))
    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(base_url="http://prom:9090"),
        session=session,
    )

    result = await provider.validate_scopes()

    assert result == {"connectivity": True}
    assert session.calls == [("http://prom:9090/-/healthy", {"ssl": True})]


@pytest.mark.asyncio
async def test_prometheus_validate_scopes_redacts_body_on_failure():
    session = FakeSession(FakeResponse(status=500, text_value="secret-token leaked"))
    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(base_url="http://prom:9090"),
        session=session,
    )

    result = await provider.validate_scopes()

    assert result["connectivity"] == "HTTP 500"
    assert "secret-token" not in result["connectivity"]


@pytest.mark.asyncio
async def test_prometheus_non_2xx_raises_provider_error_without_body():
    session = FakeSession(FakeResponse(status=500, text_value="secret-token leaked"))
    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(base_url="http://prom:9090"),
        session=session,
    )

    with pytest.raises(ProviderError, match="HTTP 500") as excinfo:
        await provider.query(query_type="instant", query="up")

    assert "secret-token" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_prometheus_verify_ssl_false_is_passed_to_request():
    session = FakeSession(FakeResponse(payload={"status": "success"}))
    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(base_url="http://prom:9090", verify_ssl=False),
        session=session,
    )

    await provider.query(query_type="instant", query="up")

    assert session.calls[0][1]["ssl"] is False


@pytest.mark.asyncio
async def test_prometheus_unknown_query_type_raises_provider_error():
    session = FakeSession(FakeResponse(payload={"status": "success"}))
    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(base_url="http://prom:9090"),
        session=session,
    )

    with pytest.raises(ProviderError, match="unsupported prometheus query_type"):
        await provider.query(query_type="bad", query="up")


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
async def test_loki_no_auth_does_not_set_auth_or_orgid_header():
    session = FakeSession(FakeResponse(payload={"data": []}))
    provider = LokiProvider(
        project_id="proj-a",
        datasource_type="loki",
        config=LokiDatasourceConfig(base_url="http://loki:3100"),
        session=session,
    )

    await provider.query(query='{job="app"}')

    request_kwargs = session.calls[0][1]
    assert "auth" not in request_kwargs
    assert "headers" not in request_kwargs


@pytest.mark.asyncio
async def test_loki_validate_scopes_hits_buildinfo_endpoint():
    session = FakeSession(FakeResponse(status=200, text_value="ok"))
    provider = LokiProvider(
        project_id="proj-a",
        datasource_type="loki",
        config=LokiDatasourceConfig(base_url="http://loki:3100"),
        session=session,
    )

    result = await provider.validate_scopes()

    assert result == {"connectivity": True}
    assert session.calls == [
        ("http://loki:3100/loki/api/v1/status/buildinfo", {"ssl": True})
    ]


@pytest.mark.asyncio
async def test_loki_validate_scopes_redacts_body_on_failure():
    session = FakeSession(FakeResponse(status=500, text_value="secret-token leaked"))
    provider = LokiProvider(
        project_id="proj-a",
        datasource_type="loki",
        config=LokiDatasourceConfig(base_url="http://loki:3100"),
        session=session,
    )

    result = await provider.validate_scopes()

    assert result["connectivity"] == "HTTP 500"
    assert "secret-token" not in result["connectivity"]


@pytest.mark.asyncio
async def test_loki_non_2xx_raises_provider_error():
    session = FakeSession(FakeResponse(status=500, text_value="boom"))
    provider = LokiProvider(
        project_id="proj-a",
        datasource_type="loki",
        config=LokiDatasourceConfig(base_url="http://loki:3100"),
        session=session,
    )

    with pytest.raises(ProviderError, match="HTTP 500"):
        await provider.query(query='{job="app"}')


@pytest.mark.asyncio
async def test_loki_non_2xx_raises_provider_error_without_body():
    session = FakeSession(FakeResponse(status=500, text_value="secret-token leaked"))
    provider = LokiProvider(
        project_id="proj-a",
        datasource_type="loki",
        config=LokiDatasourceConfig(base_url="http://loki:3100"),
        session=session,
    )

    with pytest.raises(ProviderError, match="HTTP 500") as excinfo:
        await provider.query(query='{job="app"}')

    assert "secret-token" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_loki_verify_ssl_false_is_passed_to_request():
    session = FakeSession(FakeResponse(payload={"data": []}))
    provider = LokiProvider(
        project_id="proj-a",
        datasource_type="loki",
        config=LokiDatasourceConfig(base_url="http://loki:3100", verify_ssl=False),
        session=session,
    )

    await provider.query(query='{job="app"}')

    assert session.calls[0][1]["ssl"] is False


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

    request_kwargs = session.calls[0][1]
    assert "auth" not in request_kwargs
    assert request_kwargs["headers"]["Authorization"].startswith("Basic ")


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
async def test_tempo_basic_auth_is_used_when_credentials_exist():
    session = FakeSession(FakeResponse(payload={"traceID": "abc"}))
    provider = TempoProvider(
        project_id="proj-a",
        datasource_type="tempo",
        config=TempoDatasourceConfig(
            base_url="http://tempo:3200",
            username="alice",
            password="secret",
        ),
        session=session,
    )

    await provider.query(query_type="trace", trace_id="abc")

    request_kwargs = session.calls[0][1]
    assert "auth" not in request_kwargs
    assert request_kwargs["headers"]["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_tempo_unknown_query_type_raises_provider_error():
    session = FakeSession(FakeResponse(payload={"data": []}))
    provider = TempoProvider(
        project_id="proj-a",
        datasource_type="tempo",
        config=TempoDatasourceConfig(base_url="http://tempo:3200"),
        session=session,
    )

    with pytest.raises(ProviderError, match="unsupported tempo query_type"):
        await provider.query(query_type="bad")


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
async def test_tempo_non_2xx_raises_provider_error():
    session = FakeSession(FakeResponse(status=500, text_value="boom"))
    provider = TempoProvider(
        project_id="proj-a",
        datasource_type="tempo",
        config=TempoDatasourceConfig(base_url="http://tempo:3200"),
        session=session,
    )

    with pytest.raises(ProviderError, match="HTTP 500"):
        await provider.query(query_type="search", service_name="checkout")


@pytest.mark.asyncio
async def test_tempo_non_2xx_raises_provider_error_without_body():
    session = FakeSession(FakeResponse(status=500, text_value="secret-token leaked"))
    provider = TempoProvider(
        project_id="proj-a",
        datasource_type="tempo",
        config=TempoDatasourceConfig(base_url="http://tempo:3200"),
        session=session,
    )

    with pytest.raises(ProviderError, match="HTTP 500") as excinfo:
        await provider.query(query_type="search", service_name="checkout")

    assert "secret-token" not in str(excinfo.value)


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
async def test_tempo_validate_scopes_falls_back_to_search_on_ready_failure():
    session = SequentialResponseSession(
        [
            FakeResponse(status=503, text_value="not ready"),
            FakeResponse(status=200, payload={"data": []}),
        ]
    )
    provider = TempoProvider(
        project_id="proj-a",
        datasource_type="tempo",
        config=TempoDatasourceConfig(base_url="http://tempo:3200"),
        session=session,
    )

    result = await provider.validate_scopes()

    assert result == {"connectivity": True}
    assert session.calls == [
        ("http://tempo:3200/ready", {"ssl": True}),
        ("http://tempo:3200/api/search", {"ssl": True}),
    ]


@pytest.mark.asyncio
async def test_tempo_validate_scopes_redacts_body_on_fallback_failure():
    session = SequentialResponseSession(
        [
            FakeResponse(status=503, text_value="secret-token ready failure"),
            FakeResponse(status=500, text_value="secret-token search failure"),
        ]
    )
    provider = TempoProvider(
        project_id="proj-a",
        datasource_type="tempo",
        config=TempoDatasourceConfig(base_url="http://tempo:3200"),
        session=session,
    )

    result = await provider.validate_scopes()

    assert result["connectivity"] == "HTTP 500"
    assert "secret-token" not in result["connectivity"]


@pytest.mark.asyncio
async def test_tempo_close_does_not_close_injected_session():
    session = FakeSession(FakeResponse(payload={"data": []}))
    provider = TempoProvider(
        project_id="proj-a",
        datasource_type="tempo",
        config=TempoDatasourceConfig(base_url="http://tempo:3200"),
        session=session,
    )

    await provider.close()

    assert session.closed is False


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


@pytest.mark.asyncio
async def test_lazy_session_creation_and_close_for_prometheus(monkeypatch):
    created_sessions: list[FakeSession] = []

    class FakeClientSession(FakeSession):
        def __init__(self) -> None:
            super().__init__(FakeResponse(payload={"status": "success"}))
            created_sessions.append(self)

    monkeypatch.setattr(
        "app.providers.prometheus.aiohttp.ClientSession",
        FakeClientSession,
    )

    provider = PrometheusProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=PrometheusDatasourceConfig(base_url="http://prom:9090"),
    )

    assert created_sessions == []

    await provider.query(query_type="instant", query="up")
    assert len(created_sessions) == 1
    assert created_sessions[0].closed is False

    await provider.close()
    assert created_sessions[0].closed is True
