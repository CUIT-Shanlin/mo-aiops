from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import aiohttp

from app.core.projects import LokiAuthType, LokiDatasourceConfig
from app.providers.base import BaseProvider, ProviderError


def _connectivity_summary(exc: Exception) -> str:
    message = str(exc)
    if message.startswith("HTTP "):
        return message.split(" calling ", 1)[0]
    return exc.__class__.__name__


class LokiProvider(BaseProvider[LokiDatasourceConfig]):
    CONFIG_CLASS = LokiDatasourceConfig

    def __init__(
        self,
        project_id: str,
        datasource_type: str,
        config: LokiDatasourceConfig,
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
        params = {k: v for k, v in kwargs.items() if k != "query_type" and v is not None}
        query = params.pop("query", None)
        if query is None:
            raise ProviderError("loki query is required")
        return await self._request(
            "/loki/api/v1/query_range",
            params={
                "query": query,
                "limit": params.pop("limit", 100),
                "direction": params.pop("direction", "backward"),
                **params,
            },
        )

    async def validate_scopes(self) -> dict[str, bool | str]:
        try:
            await self._request("/loki/api/v1/status/buildinfo", parse_json=False)
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
        headers: dict[str, str] = {}
        if self.config.auth_type is LokiAuthType.BASIC:
            if self.config.username and self.config.password:
                headers["Authorization"] = aiohttp.encode_basic_auth(
                    self.config.username,
                    self.config.password,
                )
        elif self.config.auth_type is LokiAuthType.X_SCOPE_ORG_ID:
            if self.config.tenant_id:
                headers["X-Scope-OrgID"] = self.config.tenant_id

        if headers:
            request_kwargs["headers"] = headers

        url = urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))
        async with session.get(url, **request_kwargs) as response:
            if response.status < 200 or response.status >= 300:
                body = await response.text()
                body = body[:200]
                raise ProviderError(f"HTTP {response.status} calling {path}: {body}")
            return await response.json() if parse_json else await response.text()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session
