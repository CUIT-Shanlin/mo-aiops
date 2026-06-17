from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.collectors.logs import collect_project_logs
from app.collectors.metrics import collect_project_metrics
from app.collectors.models import MetricsSnapshot, ProjectCollectionResult
from app.collectors.traces import collect_project_traces
from app.collectors.windows import MetricWindowStore, TraceCache
from app.core.constants import RedisKey
from app.core.projects import ProjectConfig, ProjectsConfig, iter_enabled_projects
from app.providers.factory import create_project_providers


class RedisPublisher(Protocol):
    async def publish(self, channel: str, message: str) -> Any:
        ...


ProviderMap = dict[str, Any]
ProviderFactory = Callable[[str, ProjectConfig], ProviderMap]
MetricsCollector = Callable[..., Awaitable[Any]]
LogsCollector = Callable[..., Awaitable[Any]]
TracesCollector = Callable[..., Awaitable[Any]]


class CollectorScheduler:
    """按项目编排三路采集，并发布 metrics snapshot。"""

    def __init__(
        self,
        *,
        redis: RedisPublisher,
        projects_config: ProjectsConfig,
        provider_factory: ProviderFactory = create_project_providers,
        metric_window_store: MetricWindowStore | None = None,
        trace_cache: TraceCache | None = None,
        metrics_collector: MetricsCollector = collect_project_metrics,
        logs_collector: LogsCollector = collect_project_logs,
        traces_collector: TracesCollector = collect_project_traces,
        interval_seconds: float = 15.0,
    ) -> None:
        self.redis = redis
        self.projects_config = projects_config
        self.provider_factory = provider_factory
        self.metric_window_store = metric_window_store or MetricWindowStore()
        self.trace_cache = trace_cache or TraceCache()
        self.metrics_collector = metrics_collector
        self.logs_collector = logs_collector
        self.traces_collector = traces_collector
        self.interval_seconds = interval_seconds
        self._providers: dict[str, ProviderMap] = {}
        self._stopped = asyncio.Event()
        self._log = logging.getLogger("collectors.scheduler")

    def _providers_for(self, project_id: str, project: ProjectConfig) -> ProviderMap:
        if project_id not in self._providers:
            self._providers[project_id] = self.provider_factory(project_id, project)
        return self._providers[project_id]

    async def collect_project(
        self,
        project_id: str,
        project: ProjectConfig,
    ) -> ProjectCollectionResult:
        result = ProjectCollectionResult(project_id=project_id)
        try:
            providers = self._providers_for(project_id, project)
        except Exception as exc:
            result.errors.append(f"providers: {exc.__class__.__name__}")
            self._log.warning(
                "provider initialization failed for project %s",
                project_id,
                exc_info=exc,
            )
            return result

        prometheus = providers.get("prometheus")
        if prometheus is not None:
            try:
                result.metrics = await self.metrics_collector(
                    project_id=project_id,
                    project=project,
                    provider=prometheus,
                    window_store=self.metric_window_store,
                )
            except Exception as exc:
                result.errors.append(f"metrics: {exc.__class__.__name__}")
                self._log.warning(
                    "metrics collection failed for project %s",
                    project_id,
                    exc_info=exc,
                )

        loki = providers.get("loki")
        trace_ids: set[str] = set()
        if loki is not None:
            try:
                result.logs, trace_ids = await self.logs_collector(
                    project_id=project_id,
                    provider=loki,
                    redis=self.redis,
                )
            except Exception as exc:
                result.errors.append(f"logs: {exc.__class__.__name__}")
                self._log.warning(
                    "logs collection failed for project %s",
                    project_id,
                    exc_info=exc,
                )

        tempo = providers.get("tempo")
        if tempo is not None and trace_ids:
            try:
                result.traces = await self.traces_collector(
                    project_id=project_id,
                    trace_ids=trace_ids,
                    provider=tempo,
                    trace_cache=self.trace_cache,
                )
            except Exception as exc:
                result.errors.append(f"traces: {exc.__class__.__name__}")
                self._log.warning(
                    "traces collection failed for project %s",
                    project_id,
                    exc_info=exc,
                )

        if result.metrics:
            try:
                await self.publish_metrics(project_id, result.metrics)
            except Exception as exc:
                result.errors.append(f"publish: {exc.__class__.__name__}")
                self._log.warning(
                    "metrics publish failed for project %s",
                    project_id,
                    exc_info=exc,
                )
        return result

    async def collect_once(self) -> list[ProjectCollectionResult]:
        results: list[ProjectCollectionResult] = []
        for project_id, project in iter_enabled_projects(self.projects_config):
            results.append(await self.collect_project(project_id, project))
        return results

    async def publish_metrics(self, project_id: str, metrics: list[Any]) -> None:
        snapshot = MetricsSnapshot(
            projectId=project_id,
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            metrics=metrics,
        )
        await self.redis.publish(
            RedisKey.of(project_id, RedisKey.WS_METRICS),
            snapshot.model_dump_json(),
        )

    async def run_forever(self) -> None:
        self._stopped.clear()
        while not self._stopped.is_set():
            try:
                await self.collect_once()
            except Exception as exc:
                self._log.warning("collector scheduler loop failed", exc_info=exc)
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self.interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def stop(self) -> None:
        self._stopped.set()
        for providers in self._providers.values():
            for provider in providers.values():
                close = getattr(provider, "close", None)
                if not callable(close):
                    continue
                result = close()
                if inspect.isawaitable(result):
                    await result
