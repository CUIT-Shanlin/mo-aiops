from __future__ import annotations

import asyncio
import time
from typing import Any, Generic, TypeVar

from pydantic import BaseModel


ConfigT = TypeVar("ConfigT", bound=BaseModel)


class ProviderError(RuntimeError):
    pass


class ProviderConfigurationError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class BaseProvider(Generic[ConfigT]):
    def __init__(
        self,
        project_id: str,
        datasource_type: str,
        config: ConfigT,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.project_id = project_id
        self.datasource_type = datasource_type
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.last_duration_ms: float | None = None

    def supports_query(self) -> bool:
        return type(self)._query is not BaseProvider._query

    def supports_notify(self) -> bool:
        return type(self)._notify is not BaseProvider._notify

    async def query(self, **kwargs: Any) -> Any:
        return await self._call("query", self._query, **kwargs)

    async def notify(self, **kwargs: Any) -> Any:
        return await self._call("notify", self._notify, **kwargs)

    async def validate_scopes(self) -> dict[str, bool | str]:
        return {}

    async def close(self) -> None:
        return None

    async def _call(self, action: str, func: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return await asyncio.wait_for(func(**kwargs), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(
                f"provider call timed out: action={action} project={self.project_id} "
                f"datasource={self.datasource_type}"
            ) from exc
        except NotImplementedError as exc:
            raise ProviderError(str(exc)) from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"provider call failed: action={action} project={self.project_id} "
                f"datasource={self.datasource_type}: {exc.__class__.__name__}"
            ) from None
        finally:
            self.last_duration_ms = (time.perf_counter() - start) * 1000

    async def _query(self, **kwargs: Any) -> Any:
        raise NotImplementedError("query is not supported")

    async def _notify(self, **kwargs: Any) -> Any:
        raise NotImplementedError("notify is not supported")
