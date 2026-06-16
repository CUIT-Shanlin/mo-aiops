import asyncio

import pytest

from app.providers.base import (
    BaseProvider,
    ProviderError,
    ProviderTimeoutError,
)
from pydantic import BaseModel


class DummyConfig(BaseModel):
    token: str = "secret-token"
    password: str = "secret-password"


class QueryOnlyProvider(BaseProvider[DummyConfig]):
    async def _query(self, **kwargs):
        return {"kwargs": kwargs, "project_id": self.project_id}


class SlowProvider(BaseProvider[DummyConfig]):
    async def _query(self, **kwargs):
        await asyncio.sleep(0.05)
        return kwargs


class ErrorProvider(BaseProvider[DummyConfig]):
    async def _query(self, **kwargs):
        raise ValueError("boom")


class ProviderErrorProvider(BaseProvider[DummyConfig]):
    async def _query(self, **kwargs):
        raise ProviderError("provider rejected request")


class SecretErrorProvider(BaseProvider[DummyConfig]):
    async def _query(self, **kwargs):
        raise RuntimeError("boom top-secret")


class NotifyErrorProvider(BaseProvider[DummyConfig]):
    async def _notify(self, **kwargs):
        raise RuntimeError("notify failed")


def test_capability_detection_from_overrides():
    provider = QueryOnlyProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=DummyConfig(),
    )

    assert provider.supports_query() is True
    assert provider.supports_notify() is False


@pytest.mark.asyncio
async def test_query_returns_result_and_passes_kwargs():
    provider = QueryOnlyProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=DummyConfig(),
    )

    result = await provider.query(metric="cpu", window="5m")

    assert result == {
        "kwargs": {"metric": "cpu", "window": "5m"},
        "project_id": "proj-a",
    }
    assert provider.last_duration_ms is not None


@pytest.mark.asyncio
async def test_base_query_raises_provider_error():
    provider = BaseProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=DummyConfig(),
    )

    with pytest.raises(ProviderError, match="query is not supported"):
        await provider.query()


@pytest.mark.asyncio
async def test_query_timeout_raises_provider_timeout_error():
    provider = SlowProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=DummyConfig(),
        timeout_seconds=0.01,
    )

    with pytest.raises(ProviderTimeoutError, match="provider call timed out"):
        await provider.query()

    assert provider.last_duration_ms is not None


@pytest.mark.asyncio
async def test_notify_not_implemented_raises_provider_error():
    provider = QueryOnlyProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=DummyConfig(),
    )

    with pytest.raises(ProviderError, match="notify is not supported"):
        await provider.notify()


@pytest.mark.asyncio
async def test_unknown_error_is_wrapped_and_duration_recorded():
    provider = ErrorProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=DummyConfig(token="top-secret", password="super-secret"),
    )

    with pytest.raises(ProviderError) as excinfo:
        await provider.query()

    assert "provider call failed" in str(excinfo.value)
    assert "ValueError" in str(excinfo.value)
    assert "boom" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert provider.last_duration_ms is not None


@pytest.mark.asyncio
async def test_unknown_error_redacts_secret_text_from_message():
    provider = SecretErrorProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=DummyConfig(token="top-secret", password="super-secret"),
    )

    with pytest.raises(ProviderError) as excinfo:
        await provider.query()

    message = str(excinfo.value)
    assert "top-secret" not in message
    assert "super-secret" not in message
    assert "RuntimeError" in message or "provider call failed" in message


@pytest.mark.asyncio
async def test_provider_error_is_passed_through_and_duration_recorded():
    provider = ProviderErrorProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=DummyConfig(),
    )

    with pytest.raises(ProviderError, match="provider rejected request") as excinfo:
        await provider.query()

    assert excinfo.value.__cause__ is None
    assert provider.last_duration_ms is not None


@pytest.mark.asyncio
async def test_notify_error_is_wrapped():
    provider = NotifyErrorProvider(
        project_id="proj-a",
        datasource_type="prometheus",
        config=DummyConfig(),
    )

    with pytest.raises(ProviderError) as excinfo:
        await provider.notify()

    assert "provider call failed" in str(excinfo.value)
    assert "RuntimeError" in str(excinfo.value)
    assert "notify failed" not in str(excinfo.value)
