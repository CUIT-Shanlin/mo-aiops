from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import aiohttp

from app.core.projects import TempoDatasourceConfig
from app.providers.base import BaseProvider, ProviderError


def _connectivity_summary(exc: Exception) -> str:
    message = str(exc)
    if message.startswith("HTTP "):
        return message.split(" calling ", 1)[0]
    return exc.__class__.__name__


class TempoProvider(BaseProvider[TempoDatasourceConfig]):
    CONFIG_CLASS = TempoDatasourceConfig

    def __init__(
        self,
        project_id: str,
        datasource_type: str,
        config: TempoDatasourceConfig,
        timeout_seconds: float = 10.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(project_id, datasource_type, config, timeout_seconds)
        self._session = session
        self._owns_session = session is None

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._session = None

    async def _query(self, **kwargs: Any) -> Any:
        query_type = kwargs.pop("query_type", "search")
        if query_type == "search":
            params = {k: v for k, v in kwargs.items() if v is not None}
            return await self._request("/api/search", params=params)
        if query_type == "trace":
            trace_id = kwargs.get("trace_id")
            if not trace_id:
                raise ProviderError("tempo trace_id is required")
            return await self._request(f"/api/traces/{trace_id}")
        raise ProviderError(f"unsupported tempo query_type: {query_type}")

    async def validate_scopes(self) -> dict[str, bool | str]:
        try:
            await self._request("/ready", parse_json=False)
            return {"connectivity": True}
        except ProviderError:
            try:
                await self._request("/api/search", params={})
                return {"connectivity": True}
            except ProviderError as exc:
                return {"connectivity": _connectivity_summary(exc)}

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        parse_json: bool = True,
    ) -> Any:
        session = await self._ensure_session()
        request_kwargs: dict[str, Any] = {"ssl": self.config.verify_ssl}
        request_params = {k: v for k, v in (params or {}).items() if v is not None}
        if request_params:
            request_kwargs["params"] = request_params
        if self.config.username and self.config.password:
            request_kwargs["headers"] = {
                "Authorization": aiohttp.encode_basic_auth(
                    self.config.username,
                    self.config.password,
                )
            }

        url = urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))
        async with session.get(url, **request_kwargs) as response:
            if response.status < 200 or response.status >= 300:
                raise ProviderError(f"HTTP {response.status}")
            return await response.json() if parse_json else await response.text()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session
