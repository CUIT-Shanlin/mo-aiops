"""Topology graph service built from trace, metric, alert, and log windows."""
from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from typing import Any, Iterable

from app.collectors.models import MetricSample
from app.collectors.windows import MetricWindowStore, TraceCache
from app.core.constants import RedisKey


GRAPH_CACHE_TTL_SECONDS = 60


class TopologyService:
    """Build project-scoped topology views from recent in-memory and Redis data."""

    def __init__(
        self,
        *,
        trace_cache: TraceCache | None = None,
        metric_window_store: MetricWindowStore | None = None,
        redis: Any | None = None,
    ) -> None:
        self.trace_cache = trace_cache or TraceCache(max_traces_per_project=0)
        self.metric_window_store = metric_window_store or MetricWindowStore()
        self.redis = redis

    async def graph(
        self,
        project_id: str,
        *,
        graph_filter: str = "all",
        alert_counts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Return nodes and edges for the project topology."""
        base_graph = await self._load_base_graph(project_id)
        if base_graph is None:
            base_graph = self._build_base_graph(project_id)
            await self._cache_base_graph(project_id, base_graph)
        graph = self._with_alert_counts(base_graph, alert_counts or {})
        graph = self._apply_filter(graph, graph_filter)
        return graph

    async def service_detail(
        self,
        project_id: str,
        service_id: str,
        *,
        alert_counts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Return one service node plus its immediate upstream/downstream edges."""
        base_graph = await self._load_base_graph(project_id)
        if base_graph is None:
            base_graph = self._build_base_graph(project_id)
            await self._cache_base_graph(project_id, base_graph)
        graph = self._with_alert_counts(base_graph, alert_counts or {})
        node = next(
            (item for item in graph["nodes"] if item["id"] == service_id),
            {"id": service_id, "status": "green", "alertCount": 0},
        )
        return {
            "node": node,
            "upstream": [edge for edge in graph["edges"] if edge["to"] == service_id],
            "downstream": [
                edge for edge in graph["edges"] if edge["from"] == service_id
            ],
        }

    def service_metrics(self, project_id: str, service_id: str) -> dict[str, Any]:
        """Return simple service metric cards from the latest metric window."""
        samples = {
            sample.canonicalName: sample
            for sample in self.metric_window_store.snapshot(project_id)
        }
        return {
            "service": service_id,
            "qps": self._sample_value(samples, "conn.active"),
            "errorRate": self._sample_value(samples, "http.error_rate"),
            "p99LatencyMs": self._sample_value(samples, "msg.p99_latency"),
        }

    async def logs_for_service(
        self,
        project_id: str,
        service_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Read recent error logs from Redis and filter by service."""
        entries = await self._recent_error_entries(project_id)
        filtered = [entry for entry in entries if entry.get("service") == service_id]
        start = (page - 1) * page_size
        end = start + page_size
        return {
            "total": len(filtered),
            "page": page,
            "pageSize": page_size,
            "items": filtered[start:end],
        }

    async def refresh(self, project_id: str) -> dict[str, bool]:
        """Invalidate cached topology graph."""
        if self.redis is not None and hasattr(self.redis, "delete"):
            try:
                await self.redis.delete(RedisKey.of(project_id, RedisKey.TOPOLOGY_CACHE))
            except Exception:
                pass
        return {"success": True}

    def _build_base_graph(self, project_id: str) -> dict[str, Any]:
        edge_stats: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {"count": 0.0, "error_count": 0.0, "latency_total": 0.0}
        )
        node_ids: set[str] = set()

        for _trace_id, spans in self.trace_cache.snapshot(project_id):
            span_by_id = {span.spanId: span for span in spans if span.spanId}
            for span in spans:
                if span.service:
                    node_ids.add(span.service)
                parent = span_by_id.get(span.parentSpanId or "")
                if parent is None or not parent.service or not span.service:
                    continue

                node_ids.update({parent.service, span.service})
                stat = edge_stats[(parent.service, span.service)]
                stat["count"] += 1
                stat["latency_total"] += span.durationMs
                if span.status == "error" or parent.status == "error":
                    stat["error_count"] += 1

        edges = [
            self._edge_to_dict(parent, child, stat)
            for (parent, child), stat in sorted(edge_stats.items())
        ]
        red_nodes: set[str] = set()
        for edge in edges:
            if edge["status"] == "red":
                red_nodes.update({edge["from"], edge["to"]})

        nodes = [
            {
                "id": service,
                "status": "red" if service in red_nodes else "green",
            }
            for service in sorted(node_ids)
        ]
        return {"nodes": nodes, "edges": edges}

    def _with_alert_counts(
        self,
        base_graph: dict[str, Any],
        alert_counts: dict[str, int],
    ) -> dict[str, Any]:
        graph = deepcopy(base_graph)
        node_ids = {node["id"] for node in graph["nodes"]}
        node_ids.update(alert_counts)
        red_from_edges = {
            service
            for edge in graph["edges"]
            if edge["status"] == "red"
            for service in (edge["from"], edge["to"])
        }
        node_by_id = {node["id"]: node for node in graph["nodes"]}
        graph["nodes"] = []
        for service in sorted(node_ids):
            alert_count = alert_counts.get(service, 0)
            node = dict(node_by_id.get(service, {"id": service}))
            node["status"] = (
                "red" if alert_count > 0 or service in red_from_edges else "green"
            )
            node["alertCount"] = alert_count
            graph["nodes"].append(node)
        return graph

    def _edge_to_dict(
        self,
        parent: str,
        child: str,
        stat: dict[str, float],
    ) -> dict[str, Any]:
        count = int(stat["count"])
        error_count = int(stat["error_count"])
        avg_latency = stat["latency_total"] / count if count else 0.0
        return {
            "from": parent,
            "to": child,
            "count": count,
            "errorCount": error_count,
            "avgLatencyMs": round(avg_latency, 2),
            "status": "red" if error_count > 0 else "green",
        }

    def _apply_filter(self, graph: dict[str, Any], graph_filter: str) -> dict[str, Any]:
        if graph_filter not in {"anomaly", "alert"}:
            return graph

        if graph_filter == "anomaly":
            edges = [edge for edge in graph["edges"] if edge["status"] == "red"]
            node_ids = {edge["from"] for edge in edges} | {edge["to"] for edge in edges}
            nodes = [node for node in graph["nodes"] if node["id"] in node_ids]
            return {"nodes": nodes, "edges": edges}

        nodes = [node for node in graph["nodes"] if node["alertCount"] > 0]
        node_ids = {node["id"] for node in nodes}
        edges = [
            edge
            for edge in graph["edges"]
            if edge["from"] in node_ids or edge["to"] in node_ids
        ]
        node_ids.update({edge["from"] for edge in edges})
        node_ids.update({edge["to"] for edge in edges})
        nodes = [node for node in graph["nodes"] if node["id"] in node_ids]
        return {"nodes": nodes, "edges": edges}

    async def _load_base_graph(self, project_id: str) -> dict[str, Any] | None:
        if self.redis is None or not hasattr(self.redis, "get"):
            return None
        key = RedisKey.of(project_id, RedisKey.TOPOLOGY_CACHE)
        try:
            raw = await self.redis.get(key)
        except Exception:
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        if not isinstance(raw, str):
            return None
        try:
            graph = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(graph, dict):
            return None
        if not isinstance(graph.get("nodes"), list) or not isinstance(
            graph.get("edges"), list
        ):
            return None
        return graph

    async def _cache_base_graph(self, project_id: str, graph: dict[str, Any]) -> None:
        if self.redis is None:
            return
        key = RedisKey.of(project_id, RedisKey.TOPOLOGY_CACHE)
        payload = json.dumps(graph, ensure_ascii=False)
        try:
            if hasattr(self.redis, "set"):
                await self.redis.set(key, payload, ex=GRAPH_CACHE_TTL_SECONDS)
            elif hasattr(self.redis, "setex"):
                await self.redis.setex(key, GRAPH_CACHE_TTL_SECONDS, payload)
        except Exception:
            pass

    async def _recent_error_entries(self, project_id: str) -> list[dict[str, Any]]:
        if self.redis is None:
            return []
        key = RedisKey.of(project_id, RedisKey.RECENT_ERRORS)
        raw_entries: list[Any] = []
        if hasattr(self.redis, "get"):
            try:
                raw = await self.redis.get(key)
                raw_entries.extend(self._decode_entries(raw))
            except Exception:
                pass
        if not raw_entries and hasattr(self.redis, "lrange"):
            try:
                raw_entries.extend(await self.redis.lrange(key, 0, -1))
            except Exception:
                return []
        return [entry for entry in self._normalize_entries(raw_entries) if entry]

    def _decode_entries(self, raw: Any) -> list[Any]:
        if raw is None:
            return []
        if isinstance(raw, bytes):
            raw = raw.decode()
        if not isinstance(raw, str):
            return [raw]
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(decoded, list):
            return decoded
        return [decoded]

    def _normalize_entries(self, values: Iterable[Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for value in values:
            if isinstance(value, bytes):
                value = value.decode()
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    continue
            if isinstance(value, dict):
                entries.append(value)
        return entries

    def _sample_value(
        self,
        samples: dict[str, MetricSample],
        canonical_name: str,
    ) -> float:
        sample = samples.get(canonical_name)
        return sample.value if sample is not None else 0.0
