"""Prometheus 文本指标注册表与 HTTP middleware。"""
from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, MutableMapping

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

Labels = tuple[str, str, str]
UNMATCHED_ROUTE_LABEL = "__unmatched__"


class MetricsRegistry:
    """进程内 Prometheus 指标注册表。"""

    def __init__(self) -> None:
        self._requests_total: MutableMapping[Labels, int] = defaultdict(int)
        self._duration_sum: MutableMapping[Labels, float] = defaultdict(float)

    def record_http_request(
        self, *, method: str, path: str, status: int, duration_seconds: float
    ) -> None:
        """记录一次 HTTP 请求。"""
        labels = (method.upper(), path, str(status))
        self._requests_total[labels] += 1
        self._duration_sum[labels] += duration_seconds

    def render(self) -> str:
        """渲染 Prometheus text exposition format。"""
        lines = [
            "# HELP mo_chat_aiops_http_requests_total Total HTTP requests.",
            "# TYPE mo_chat_aiops_http_requests_total counter",
        ]
        for labels, count in sorted(self._requests_total.items()):
            lines.append(
                f"mo_chat_aiops_http_requests_total{self._format_labels(labels)} {count}"
            )

        lines.extend(
            [
                "# HELP mo_chat_aiops_http_request_duration_seconds_sum Total HTTP request duration seconds.",
                "# TYPE mo_chat_aiops_http_request_duration_seconds_sum counter",
            ]
        )
        for labels, duration_sum in sorted(self._duration_sum.items()):
            lines.append(
                "mo_chat_aiops_http_request_duration_seconds_sum"
                f"{self._format_labels(labels)} {duration_sum:.9f}"
            )

        lines.extend(
            [
                "# HELP mo_chat_aiops_http_request_duration_seconds_count HTTP request duration count.",
                "# TYPE mo_chat_aiops_http_request_duration_seconds_count counter",
            ]
        )
        for labels, count in sorted(self._requests_total.items()):
            lines.append(
                "mo_chat_aiops_http_request_duration_seconds_count"
                f"{self._format_labels(labels)} {count}"
            )

        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_labels(labels: Labels) -> str:
        method, path, status = labels
        return (
            "{"
            f'method="{_escape_label_value(method)}",'
            f'path="{_escape_label_value(path)}",'
            f'status="{_escape_label_value(status)}"'
            "}"
        )


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录 HTTP 请求指标，跳过 Prometheus 抓取端点。"""

    def __init__(self, app: ASGIApp, registry: MetricsRegistry) -> None:
        super().__init__(app)
        self._registry = registry

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        started_at = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            self._registry.record_http_request(
                method=request.method,
                path=_request_path_label(request),
                status=status_code,
                duration_seconds=time.perf_counter() - started_at,
            )


def _request_path_label(request: Request) -> str:
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None)
    if path_format:
        return str(path_format)
    path = getattr(route, "path", None)
    if path:
        return str(path)
    return UNMATCHED_ROUTE_LABEL


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
