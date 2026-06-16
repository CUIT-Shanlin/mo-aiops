from __future__ import annotations

from typing import Any

from kubernetes_asyncio import client, config

from app.core.projects import KubernetesDatasourceConfig, KubernetesMode
from app.providers.base import BaseProvider, ProviderError


def _connectivity_summary(exc: Exception) -> dict[str, bool | str]:
    parts: list[str] = [exc.__class__.__name__]
    status = getattr(exc, "status", None)
    if status is not None:
        parts.append(f"status={status}")
    reason = getattr(exc, "reason", None)
    if reason:
        parts.append(f"reason={reason}")
    message = " ".join(parts)
    return {"connectivity": False, "error": message[:200]}


class KubernetesProvider(BaseProvider[KubernetesDatasourceConfig]):
    CONFIG_CLASS = KubernetesDatasourceConfig

    def __init__(
        self,
        project_id: str,
        datasource_type: str,
        config: KubernetesDatasourceConfig,
        timeout_seconds: float = 10.0,
        version_api: Any | None = None,
        core_v1_api: Any | None = None,
    ) -> None:
        super().__init__(project_id, datasource_type, config, timeout_seconds)
        self._version_api = version_api
        self._core_v1_api = core_v1_api
        self._api_client: Any | None = None
        self._owns_api_client = False

    async def close(self) -> None:
        if self._api_client is not None and self._owns_api_client:
            close = getattr(self._api_client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
        self._api_client = None
        self._owns_api_client = False

    async def _query(self, **kwargs: Any) -> Any:
        command_type = kwargs.pop("command_type", None)
        if command_type == "version":
            return await (await self._get_version_api()).get_code()
        if command_type == "list_pods":
            namespace = kwargs.pop("namespace", None)
            core_v1_api = await self._get_core_v1_api()
            if namespace:
                return await core_v1_api.list_namespaced_pod(namespace)
            return await core_v1_api.list_pod_for_all_namespaces()
        if command_type == "list_nodes":
            return await (await self._get_core_v1_api()).list_node()
        raise ProviderError(f"unsupported kubernetes command_type: {command_type}")

    async def validate_scopes(self) -> dict[str, bool | str]:
        try:
            await (await self._get_version_api()).get_code()
            return {"connectivity": True}
        except Exception as exc:
            return _connectivity_summary(exc)

    async def _get_api_client(self) -> Any:
        if self._api_client is not None:
            return self._api_client

        if self.config.mode is KubernetesMode.IN_CLUSTER:
            config.load_incluster_config()
            self._api_client = client.ApiClient()
        elif self.config.mode is KubernetesMode.KUBECONFIG:
            await config.load_kube_config(config_file=self.config.kubeconfig_path)
            self._api_client = client.ApiClient()
        elif self.config.mode is KubernetesMode.TOKEN:
            configuration = client.Configuration(host=self.config.api_server)
            configuration.verify_ssl = self.config.verify_ssl
            if self.config.token:
                configuration.api_key["BearerToken"] = self.config.token
                configuration.api_key_prefix["BearerToken"] = "Bearer"
            self._api_client = client.ApiClient(configuration=configuration)
        else:  # pragma: no cover - KubernetesMode is validated by Pydantic
            raise ProviderError(f"unsupported kubernetes mode: {self.config.mode}")

        self._owns_api_client = True
        return self._api_client

    async def _get_version_api(self) -> Any:
        if self._version_api is None:
            api_client = await self._get_api_client()
            self._version_api = client.VersionApi(api_client)
        return self._version_api

    async def _get_core_v1_api(self) -> Any:
        if self._core_v1_api is None:
            api_client = await self._get_api_client()
            self._core_v1_api = client.CoreV1Api(api_client)
        return self._core_v1_api
