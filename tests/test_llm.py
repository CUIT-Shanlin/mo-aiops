import asyncio

import pytest
from pydantic import BaseModel

from app.agent.llm import (
    LLMClient,
    LLMNotConfiguredError,
    LLMStructuredOutputError,
    build_chat_model,
)
from app.core.config import Settings


class ActionSchema(BaseModel):
    action: str
    confidence: float


def test_from_settings_raises_when_llm_not_configured():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379",
        jwt_secret="secret",
    )

    with pytest.raises(LLMNotConfiguredError, match="LLM is not configured"):
        LLMClient.from_settings(settings)


def test_structured_model_path_returns_pydantic_instance():
    class FakeStructuredModel:
        async def ainvoke(self, messages):
            return ActionSchema(action="restart", confidence=0.8)

    class FakeModel:
        def with_structured_output(self, schema):
            self.schema = schema
            return FakeStructuredModel()

    client = LLMClient(FakeModel(), max_retries=1)
    result = asyncio.run(
        client.ainvoke_structured(
            [{"role": "user", "content": "restart"}], ActionSchema
        )
    )

    assert result == ActionSchema(action="restart", confidence=0.8)


def test_structured_model_dict_result_is_validated():
    class FakeStructuredModel:
        async def ainvoke(self, messages):
            return {"action": "restart", "confidence": 0.8}

    class FakeModel:
        def with_structured_output(self, schema):
            return FakeStructuredModel()

    client = LLMClient(FakeModel(), max_retries=1)
    result = asyncio.run(
        client.ainvoke_structured(
            [{"role": "user", "content": "restart"}], ActionSchema
        )
    )

    assert result == ActionSchema(action="restart", confidence=0.8)


def test_json_text_fallback_parses_content():
    class FakeJsonModel:
        async def ainvoke(self, messages):
            return type(
                "FakeMessage",
                (),
                {"content": '{"action":"restart","confidence":0.8}'},
            )()

    client = LLMClient(FakeJsonModel(), max_retries=1)
    result = asyncio.run(
        client.ainvoke_structured(
            [{"role": "user", "content": "restart"}], ActionSchema
        )
    )

    assert result == ActionSchema(action="restart", confidence=0.8)


def test_invalid_json_output_raises_structured_output_error():
    class FakeJsonModel:
        async def ainvoke(self, messages):
            return type("FakeMessage", (), {"content": "not json"})()

    client = LLMClient(FakeJsonModel(), max_retries=1)

    with pytest.raises(
        LLMStructuredOutputError, match="LLM structured output validation failed"
    ):
        asyncio.run(
            client.ainvoke_structured(
                [{"role": "user", "content": "restart"}], ActionSchema
            )
        )


def test_model_runtime_error_is_redacted_and_chain_is_dropped():
    class BrokenModel:
        async def ainvoke(self, messages):
            raise RuntimeError("secret prompt/api-key")

    client = LLMClient(BrokenModel(), max_retries=1)

    with pytest.raises(LLMStructuredOutputError) as exc_info:
        asyncio.run(
            client.ainvoke_structured(
                [{"role": "user", "content": "restart"}], ActionSchema
            )
        )

    assert str(exc_info.value) == "LLM structured output validation failed"
    assert "secret prompt/api-key" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_not_implemented_structured_output_falls_back_to_json():
    class FakeJsonModel:
        def __init__(self):
            self.calls = 0

        def with_structured_output(self, schema):
            raise NotImplementedError

        async def ainvoke(self, messages):
            self.calls += 1
            return type(
                "FakeMessage",
                (),
                {"content": '{"action":"restart","confidence":0.8}'},
            )()

    model = FakeJsonModel()
    client = LLMClient(model, max_retries=1)
    result = asyncio.run(
        client.ainvoke_structured(
            [{"role": "user", "content": "restart"}], ActionSchema
        )
    )

    assert result == ActionSchema(action="restart", confidence=0.8)
    assert model.calls == 1


def test_structured_validation_failure_retries_without_raw_json_fallback():
    class FakeStructuredModel:
        async def ainvoke(self, messages):
            return {"action": "restart"}

    class FakeModel:
        def __init__(self):
            self.fallback_calls = 0

        def with_structured_output(self, schema):
            return FakeStructuredModel()

        async def ainvoke(self, messages):
            self.fallback_calls += 1
            return type(
                "FakeMessage",
                (),
                {"content": '{"action":"restart","confidence":0.8}'},
            )()

    model = FakeModel()
    client = LLMClient(model, max_retries=2)

    with pytest.raises(
        LLMStructuredOutputError, match="LLM structured output validation failed"
    ):
        asyncio.run(
            client.ainvoke_structured(
                [{"role": "user", "content": "restart"}], ActionSchema
            )
        )

    assert model.fallback_calls == 0


def test_llm_client_rejects_non_positive_retry_count():
    class FakeJsonModel:
        async def ainvoke(self, messages):
            return type(
                "FakeMessage",
                (),
                {"content": '{"action":"restart","confidence":0.8}'},
            )()

    with pytest.raises(ValueError, match="max_retries must be at least 1"):
        LLMClient(FakeJsonModel(), max_retries=0)


def test_structured_failures_retry_structured_path_only_when_supported():
    class FailingStructuredModel:
        async def ainvoke(self, messages):
            raise ValueError("structured failed")

    class CountingModel:
        def __init__(self):
            self.structured_calls = 0
            self.raw_calls = 0

        def with_structured_output(self, schema):
            model = self

            class StructuredWrapper:
                async def ainvoke(self, messages):
                    model.structured_calls += 1
                    return FailingStructuredModel()

            return StructuredWrapper()

        async def ainvoke(self, messages):
            self.raw_calls += 1
            raise ValueError("raw failed")

    model = CountingModel()
    client = LLMClient(model, max_retries=2)

    with pytest.raises(
        LLMStructuredOutputError, match="LLM structured output validation failed"
    ):
        asyncio.run(
            client.ainvoke_structured(
                [{"role": "user", "content": "restart"}], ActionSchema
            )
        )

    assert model.structured_calls == 2
    assert model.raw_calls == 0


def test_timeout_raises_structured_output_error():
    class SlowModel:
        async def ainvoke(self, messages):
            await asyncio.sleep(0.05)
            return type(
                "FakeMessage",
                (),
                {"content": '{"action":"restart","confidence":0.8}'},
            )()

    client = LLMClient(SlowModel(), max_retries=1, timeout_seconds=0.01)

    with pytest.raises(
        LLMStructuredOutputError, match="LLM structured output validation failed"
    ):
        asyncio.run(
            client.ainvoke_structured(
                [{"role": "user", "content": "restart"}], ActionSchema
            )
        )


def test_build_chat_model_passes_openai_compatible_config(monkeypatch):
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.agent.llm.ChatOpenAI", FakeChatOpenAI)

    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379",
        jwt_secret="secret",
        llm_provider="qwen",
        llm_api_key="api-key",
        llm_base_url="https://example.com/v1",
        llm_model="qwen-plus",
    )

    model = build_chat_model(settings)

    assert isinstance(model, FakeChatOpenAI)
    assert captured == {
        "model": "qwen-plus",
        "api_key": "api-key",
        "base_url": "https://example.com/v1",
    }


def test_build_chat_model_allows_openai_without_base_url(monkeypatch):
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.agent.llm.ChatOpenAI", FakeChatOpenAI)

    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379",
        jwt_secret="secret",
        llm_provider="openai",
        llm_api_key="api-key",
        llm_model="gpt-4o-mini",
    )

    model = build_chat_model(settings)

    assert isinstance(model, FakeChatOpenAI)
    assert captured == {
        "model": "gpt-4o-mini",
        "api_key": "api-key",
    }


def test_build_chat_model_requires_qwen_base_url_without_leaking_secret():
    secret = "super-secret-token"
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379",
        jwt_secret="secret",
        llm_provider="qwen",
        llm_api_key=secret,
        llm_model="qwen-plus",
    )

    with pytest.raises(LLMNotConfiguredError, match="LLM base_url is required for qwen provider"):
        build_chat_model(settings)


def test_build_chat_model_rejects_unknown_provider_without_leaking_secret():
    secret = "super-secret-token"
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379",
        jwt_secret="secret",
        llm_provider="anthropic",
        llm_api_key=secret,
        llm_model="claude-3",
    )

    with pytest.raises(LLMNotConfiguredError, match="unsupported LLM provider 'anthropic'"):
        build_chat_model(settings)


def test_build_chat_model_missing_config_does_not_leak_secret():
    secret = "super-secret-token"
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379",
        jwt_secret="secret",
        llm_provider="openai",
        llm_api_key=secret,
    )

    with pytest.raises(LLMNotConfiguredError) as exc_info:
        build_chat_model(settings)

    message = str(exc_info.value)
    assert "LLM is not configured" in message
    assert secret not in message
