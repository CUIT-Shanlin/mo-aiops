import asyncio
import pytest
import time

from app.core.projects import KubernetesDatasourceConfig, KubernetesMode
from app.providers.base import ProviderError
from app.providers.kubernetes import KubernetesProvider


class FakeVersionApi:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def get_code(self):
        self.calls += 1
        return self.result


class FakeCoreV1Api:
    def __init__(self, pods_result=None, nodes_result=None):
        self.pods_result = pods_result
        self.nodes_result = nodes_result
        self.calls = []

    async def list_pod_for_all_namespaces(self):
        self.calls.append(("list_pod_for_all_namespaces",))
        return self.pods_result

    async def list_namespaced_pod(self, namespace):
        self.calls.append(("list_namespaced_pod", namespace))
        return self.pods_result

    async def list_node(self):
        self.calls.append(("list_node",))
        return self.nodes_result


class FakeApiClient:
    def __init__(self):
        self.closed = False
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1
        self.closed = True


@pytest.mark.asyncio
async def test_query_version_uses_injected_version_api():
    version_api = FakeVersionApi({"gitVersion": "v1.30.0"})
    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.TOKEN, api_server="https://k8s.example"),
        version_api=version_api,
    )

    result = await provider.query(command_type="version")

    assert result == {"gitVersion": "v1.30.0"}
    assert version_api.calls == 1


@pytest.mark.asyncio
async def test_query_list_pods_uses_all_namespaces_without_namespace():
    core_v1_api = FakeCoreV1Api(pods_result={"items": ["pod-a", "pod-b"]})
    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.TOKEN, api_server="https://k8s.example"),
        core_v1_api=core_v1_api,
    )

    result = await provider.query(command_type="list_pods")

    assert result == {"items": ["pod-a", "pod-b"]}
    assert core_v1_api.calls == [("list_pod_for_all_namespaces",)]


@pytest.mark.asyncio
async def test_query_list_pods_uses_namespace_when_provided():
    core_v1_api = FakeCoreV1Api(pods_result={"items": ["pod-a"]})
    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.TOKEN, api_server="https://k8s.example"),
        core_v1_api=core_v1_api,
    )

    result = await provider.query(command_type="list_pods", namespace="default")

    assert result == {"items": ["pod-a"]}
    assert core_v1_api.calls == [("list_namespaced_pod", "default")]


@pytest.mark.asyncio
async def test_query_list_nodes_uses_core_api():
    core_v1_api = FakeCoreV1Api(nodes_result={"items": ["node-a"]})
    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.TOKEN, api_server="https://k8s.example"),
        core_v1_api=core_v1_api,
    )

    result = await provider.query(command_type="list_nodes")

    assert result == {"items": ["node-a"]}
    assert core_v1_api.calls == [("list_node",)]


@pytest.mark.asyncio
async def test_query_unknown_command_type_raises_provider_error():
    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.TOKEN, api_server="https://k8s.example"),
    )

    with pytest.raises(ProviderError, match="unsupported kubernetes command_type"):
        await provider.query(command_type="unsupported")


@pytest.mark.asyncio
async def test_validate_scopes_returns_connectivity_true_on_success():
    version_api = FakeVersionApi({"gitVersion": "v1.30.0"})
    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.TOKEN, api_server="https://k8s.example"),
        version_api=version_api,
    )

    result = await provider.validate_scopes()

    assert result == {"connectivity": True}


@pytest.mark.asyncio
async def test_validate_scopes_redacts_secret_on_failure():
    class FailingVersionApi:
        async def get_code(self):
            raise RuntimeError("secret-token leaked")

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(
            mode=KubernetesMode.TOKEN,
            api_server="https://k8s.example",
            token="secret-token",
        ),
        version_api=FailingVersionApi(),
    )

    result = await provider.validate_scopes()

    assert result["connectivity"] is False
    assert "secret-token" not in result["error"]
    assert result["error"]


@pytest.mark.asyncio
async def test_validate_scopes_includes_status_and_reason():
    class FakeApiException(Exception):
        def __init__(self):
            self.status = 403
            self.reason = "Forbidden"

    class FailingVersionApi:
        async def get_code(self):
            raise FakeApiException()

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(
            mode=KubernetesMode.TOKEN,
            api_server="https://k8s.example",
            token="secret-token",
        ),
        version_api=FailingVersionApi(),
    )

    result = await provider.validate_scopes()

    assert result["connectivity"] is False
    assert "status=403" in result["error"]
    assert "reason=Forbidden" in result["error"]
    assert "secret-token" not in result["error"]


@pytest.mark.asyncio
async def test_validate_scopes_redacts_runtime_error_message():
    class FailingVersionApi:
        async def get_code(self):
            raise RuntimeError("boom secret-token")

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(
            mode=KubernetesMode.TOKEN,
            api_server="https://k8s.example",
            token="secret-token",
        ),
        version_api=FailingVersionApi(),
    )

    result = await provider.validate_scopes()

    assert result == {"connectivity": False, "error": "RuntimeError"}
    assert "secret-token" not in result["error"]


@pytest.mark.asyncio
async def test_validate_scopes_times_out_quickly():
    class SlowVersionApi:
        async def get_code(self):
            await asyncio.sleep(0.05)
            return {"gitVersion": "v1.30.4"}

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(
            mode=KubernetesMode.TOKEN,
            api_server="https://k8s.example",
        ),
        timeout_seconds=0.01,
        version_api=SlowVersionApi(),
    )

    start = time.perf_counter()
    result = await provider.validate_scopes()
    elapsed = time.perf_counter() - start

    assert result["connectivity"] is False
    assert "TimeoutError" in result["error"] or "timeout" in result["error"].lower()
    assert elapsed < 0.03


@pytest.mark.asyncio
async def test_token_mode_get_api_client_sets_configuration(monkeypatch):
    captured = {}

    class FakeConfiguration:
        def __init__(self, host=None):
            self.host = host
            self.verify_ssl = None
            self.api_key = {}
            self.api_key_prefix = {}

    def fake_api_client(configuration=None):
        captured["configuration"] = configuration
        return FakeApiClient()

    monkeypatch.setattr("app.providers.kubernetes.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("app.providers.kubernetes.client.ApiClient", fake_api_client)

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(
            mode=KubernetesMode.TOKEN,
            api_server="https://k8s.example",
            token="secret-token",
            verify_ssl=False,
        ),
    )

    api_client = await provider._get_api_client()

    assert isinstance(api_client, FakeApiClient)
    configuration = captured["configuration"]
    assert configuration.host == "https://k8s.example"
    assert configuration.verify_ssl is False
    assert configuration.api_key["BearerToken"] == "secret-token"
    assert configuration.api_key_prefix["BearerToken"] == "Bearer"


@pytest.mark.asyncio
async def test_in_cluster_query_creates_api_client(monkeypatch):
    captured = {}

    class FakeApiClient:
        def __init__(self):
            captured["api_client_created"] = True

    class FakeVersionApi:
        def __init__(self, api_client):
            captured["version_api_client"] = api_client

        async def get_code(self):
            return {"gitVersion": "v1.30.2"}

    def fake_load_incluster_config():
        captured["load_incluster_config"] = True

    monkeypatch.setattr("app.providers.kubernetes.config.load_incluster_config", fake_load_incluster_config)
    monkeypatch.setattr("app.providers.kubernetes.client.ApiClient", FakeApiClient)
    monkeypatch.setattr("app.providers.kubernetes.client.VersionApi", FakeVersionApi)

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.IN_CLUSTER),
    )

    result = await provider.query(command_type="version")

    assert result == {"gitVersion": "v1.30.2"}
    assert captured["load_incluster_config"] is True
    assert captured["api_client_created"] is True
    assert isinstance(captured["version_api_client"], FakeApiClient)


@pytest.mark.asyncio
async def test_kubeconfig_query_creates_api_client(monkeypatch):
    captured = {}

    class FakeApiClient:
        def __init__(self):
            captured["api_client_created"] = True

    class FakeVersionApi:
        def __init__(self, api_client):
            captured["version_api_client"] = api_client

        async def get_code(self):
            return {"gitVersion": "v1.30.3"}

    async def fake_load_kube_config(config_file=None):
        captured["config_file"] = config_file

    monkeypatch.setattr("app.providers.kubernetes.config.load_kube_config", fake_load_kube_config)
    monkeypatch.setattr("app.providers.kubernetes.client.ApiClient", FakeApiClient)
    monkeypatch.setattr("app.providers.kubernetes.client.VersionApi", FakeVersionApi)

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(
            mode=KubernetesMode.KUBECONFIG,
            kubeconfig_path="/tmp/kubeconfig",
        ),
    )

    result = await provider.query(command_type="version")

    assert result == {"gitVersion": "v1.30.3"}
    assert captured["config_file"] == "/tmp/kubeconfig"
    assert captured["api_client_created"] is True
    assert isinstance(captured["version_api_client"], FakeApiClient)


@pytest.mark.asyncio
async def test_query_version_creates_version_api_from_owned_api_client(monkeypatch):
    captured = {}

    class FakeConfiguration:
        def __init__(self, host=None):
            self.host = host
            self.verify_ssl = None
            self.api_key = {}
            self.api_key_prefix = {}

    class FakeVersionApi:
        def __init__(self, api_client):
            captured["api_client"] = api_client

        async def get_code(self):
            return {"gitVersion": "v1.30.1"}

    def fake_api_client(configuration=None):
        captured["configuration"] = configuration
        return FakeApiClient()

    monkeypatch.setattr("app.providers.kubernetes.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("app.providers.kubernetes.client.ApiClient", fake_api_client)
    monkeypatch.setattr("app.providers.kubernetes.client.VersionApi", FakeVersionApi)

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(
            mode=KubernetesMode.TOKEN,
            api_server="https://k8s.example",
            token="secret-token",
        ),
    )

    result = await provider.query(command_type="version")

    assert result == {"gitVersion": "v1.30.1"}
    assert isinstance(captured["api_client"], FakeApiClient)
    assert captured["configuration"].host == "https://k8s.example"
    assert provider._api_client is captured["api_client"]


@pytest.mark.asyncio
async def test_query_list_nodes_creates_core_v1_api_from_owned_api_client(monkeypatch):
    captured = {}

    class FakeConfiguration:
        def __init__(self, host=None):
            self.host = host
            self.verify_ssl = None
            self.api_key = {}
            self.api_key_prefix = {}

    class FakeCoreV1Api:
        def __init__(self, api_client):
            captured["api_client"] = api_client

        async def list_node(self):
            return {"items": ["node-a"]}

    def fake_api_client(configuration=None):
        captured["configuration"] = configuration
        return FakeApiClient()

    monkeypatch.setattr("app.providers.kubernetes.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("app.providers.kubernetes.client.ApiClient", fake_api_client)
    monkeypatch.setattr("app.providers.kubernetes.client.CoreV1Api", FakeCoreV1Api)

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(
            mode=KubernetesMode.TOKEN,
            api_server="https://k8s.example",
            token="secret-token",
        ),
    )

    result = await provider.query(command_type="list_nodes")

    assert result == {"items": ["node-a"]}
    assert isinstance(captured["api_client"], FakeApiClient)
    assert captured["configuration"].host == "https://k8s.example"
    assert provider._api_client is captured["api_client"]


@pytest.mark.asyncio
async def test_close_closes_owned_api_client():
    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.TOKEN, api_server="https://k8s.example"),
    )
    provider._api_client = FakeApiClient()
    provider._owns_api_client = True

    await provider.close()

    assert provider._api_client is None
    assert provider._owns_api_client is False


@pytest.mark.asyncio
async def test_close_is_idempotent():
    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.TOKEN, api_server="https://k8s.example"),
    )
    client = FakeApiClient()
    provider._api_client = client
    provider._owns_api_client = True

    await provider.close()
    await provider.close()

    assert client.close_calls == 1
    assert provider._api_client is None
    assert provider._owns_api_client is False


@pytest.mark.asyncio
async def test_close_clears_cached_apis_and_recreates_them_after_reopen(monkeypatch):
    captured = {"api_client_ids": [], "version_api_ids": []}

    class FakeConfiguration:
        def __init__(self, host=None):
            self.host = host
            self.verify_ssl = None
            self.api_key = {}
            self.api_key_prefix = {}

    class FakeApiClient:
        def __init__(self, configuration=None):
            self.configuration = configuration
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeVersionApi:
        def __init__(self, api_client):
            captured["api_client_ids"].append(id(api_client))
            captured["version_api_ids"].append(id(self))
            self.api_client = api_client

        async def get_code(self):
            return {"gitVersion": "v1.30.5"}

    monkeypatch.setattr("app.providers.kubernetes.client.Configuration", FakeConfiguration)
    monkeypatch.setattr("app.providers.kubernetes.client.ApiClient", FakeApiClient)
    monkeypatch.setattr("app.providers.kubernetes.client.VersionApi", FakeVersionApi)

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(
            mode=KubernetesMode.TOKEN,
            api_server="https://k8s.example",
        ),
    )

    first = await provider.query(command_type="version")
    first_api_client = provider._api_client
    first_version_api = provider._version_api

    await provider.close()

    second = await provider.query(command_type="version")

    assert first == {"gitVersion": "v1.30.5"}
    assert second == {"gitVersion": "v1.30.5"}
    assert first_api_client is not None
    assert first_version_api is not None
    assert first_api_client.closed is True
    assert provider._api_client is not first_api_client
    assert provider._version_api is not first_version_api
    assert len(captured["api_client_ids"]) == 2
    assert len(set(captured["api_client_ids"])) == 2


@pytest.mark.asyncio
async def test_close_preserves_injected_version_api(monkeypatch):
    class InjectedVersionApi:
        def __init__(self):
            self.calls = 0

        async def get_code(self):
            self.calls += 1
            return {"gitVersion": "v1.30.6"}

    injected_version_api = InjectedVersionApi()
    created = {"api_client": 0, "version_api": 0}

    class FailingApiClient:
        def __init__(self, configuration=None):
            created["api_client"] += 1

    class FailingVersionApi:
        def __init__(self, api_client):
            created["version_api"] += 1

        async def get_code(self):
            raise AssertionError("should not be constructed")

    monkeypatch.setattr("app.providers.kubernetes.client.ApiClient", FailingApiClient)
    monkeypatch.setattr("app.providers.kubernetes.client.VersionApi", FailingVersionApi)

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.TOKEN, api_server="https://k8s.example"),
        version_api=injected_version_api,
    )

    first = await provider.query(command_type="version")
    await provider.close()
    second = await provider.query(command_type="version")

    assert first == {"gitVersion": "v1.30.6"}
    assert second == {"gitVersion": "v1.30.6"}
    assert injected_version_api.calls == 2
    assert created["api_client"] == 0
    assert created["version_api"] == 0


@pytest.mark.asyncio
async def test_close_preserves_injected_core_v1_api(monkeypatch):
    class InjectedCoreV1Api:
        def __init__(self):
            self.calls = []

        async def list_node(self):
            self.calls.append("list_node")
            return {"items": ["node-a"]}

    injected_core_v1_api = InjectedCoreV1Api()
    created = {"api_client": 0, "core_v1_api": 0}

    class FailingApiClient:
        def __init__(self, configuration=None):
            created["api_client"] += 1

    class FailingCoreV1Api:
        def __init__(self, api_client):
            created["core_v1_api"] += 1

        async def list_node(self):
            raise AssertionError("should not be constructed")

    monkeypatch.setattr("app.providers.kubernetes.client.ApiClient", FailingApiClient)
    monkeypatch.setattr("app.providers.kubernetes.client.CoreV1Api", FailingCoreV1Api)

    provider = KubernetesProvider(
        project_id="proj-a",
        datasource_type="kubernetes",
        config=KubernetesDatasourceConfig(mode=KubernetesMode.TOKEN, api_server="https://k8s.example"),
        core_v1_api=injected_core_v1_api,
    )

    first = await provider.query(command_type="list_nodes")
    await provider.close()
    second = await provider.query(command_type="list_nodes")

    assert first == {"items": ["node-a"]}
    assert second == {"items": ["node-a"]}
    assert injected_core_v1_api.calls == ["list_node", "list_node"]
    assert created["api_client"] == 0
    assert created["core_v1_api"] == 0
