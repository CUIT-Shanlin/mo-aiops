from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from app.core.config import Settings, get_settings

ChatOpenAI: Any
_chat_openai_import_error: Exception | None

try:
    from langchain_openai import ChatOpenAI as _ImportedChatOpenAI
except Exception as exc:  # pragma: no cover - import-time dependency guard
    ChatOpenAI = None
    _chat_openai_import_error = exc
else:  # pragma: no cover - import-time dependency guard
    ChatOpenAI = _ImportedChatOpenAI
    _chat_openai_import_error = None


class LLMError(RuntimeError):
    """LLM 适配层基础异常。"""


class LLMNotConfiguredError(LLMError):
    """LLM 关键配置缺失。"""


class LLMStructuredOutputError(LLMError):
    """LLM 结构化输出解析失败。"""


def build_chat_model(settings: Settings | None = None) -> Any:
    """从全局配置构造 LangChain chat model。"""

    cfg = settings or get_settings()
    if not cfg.llm_provider or not cfg.llm_model or not cfg.llm_api_key:
        raise LLMNotConfiguredError("LLM is not configured")
    if ChatOpenAI is None:  # pragma: no cover - dependency import guard
        raise LLMError("LLM backend is unavailable") from _chat_openai_import_error

    provider = cfg.llm_provider.lower()
    if provider not in {"openai", "qwen"}:
        raise LLMNotConfiguredError(f"unsupported LLM provider '{cfg.llm_provider}'")
    if provider == "qwen" and not cfg.llm_base_url:
        raise LLMNotConfiguredError("LLM base_url is required for qwen provider")

    kwargs: dict[str, Any] = {
        "model": cfg.llm_model,
        "api_key": cfg.llm_api_key,
    }
    if cfg.llm_base_url:
        kwargs["base_url"] = cfg.llm_base_url

    return ChatOpenAI(**kwargs)


class LLMClient:
    def __init__(
        self,
        model: Any,
        max_retries: int = 3,
        timeout_seconds: float = 30.0,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        self.model = model
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "LLMClient":
        return cls(build_chat_model(settings))

    async def ainvoke_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> BaseModel:
        for attempt in range(self.max_retries):
            try:
                result = await asyncio.wait_for(
                    self._ainvoke_structured_once(messages, schema),
                    timeout=self.timeout_seconds,
                )
                return result
            except Exception:  # noqa: BLE001
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(0.2 * (2**attempt))

        raise LLMStructuredOutputError("LLM structured output validation failed") from None

    async def _ainvoke_structured_once(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
    ) -> BaseModel:
        structured_model = self._build_structured_model(schema)
        if structured_model is not None:
            structured_result = await structured_model.ainvoke(messages)
            return self._coerce_schema_result(structured_result, schema)

        fallback_result = await self.model.ainvoke(messages)
        return self._parse_fallback_result(fallback_result, schema)

    def _build_structured_model(
        self, schema: type[BaseModel]
    ) -> Any | None:
        with_structured_output = getattr(self.model, "with_structured_output", None)
        if with_structured_output is None:
            return None

        try:
            return with_structured_output(schema)
        except NotImplementedError:
            return None

    def _coerce_schema_result(
        self,
        result: Any,
        schema: type[BaseModel],
    ) -> BaseModel:
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)

    def _parse_fallback_result(
        self,
        result: Any,
        schema: type[BaseModel],
    ) -> BaseModel:
        content = getattr(result, "content", result)
        if isinstance(content, schema):
            return content
        if isinstance(content, str):
            parsed = json.loads(content)
            return schema.model_validate(parsed)
        return schema.model_validate(content)
