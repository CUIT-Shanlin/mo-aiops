"""结构化 JSON 日志：dictConfig + contextvars 注入 trace_id/project_id。"""
import logging
import sys
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

from app.core.constants import SERVICE_NAME

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
project_id_var: ContextVar[str] = ContextVar("project_id", default="")


def set_trace_id(value: str) -> None:
    """设置当前请求 trace_id（中间件调用）。"""
    trace_id_var.set(value)


def set_project_id(value: str) -> None:
    """设置当前请求 project_id。"""
    project_id_var.set(value)


class ContextFilter(logging.Filter):
    """把 contextvars 注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = SERVICE_NAME
        record.trace_id = trace_id_var.get()
        record.project_id = project_id_var.get()
        return True


class AIOpsLogHandler(logging.StreamHandler):
    """本服务安装的日志 handler，避免覆盖测试或宿主进程 handler。"""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr
        super().emit(record)


def setup_logging(log_format: str = "json") -> None:
    """配置根 logger；dev 人类可读，json 结构化。"""
    root = logging.getLogger()
    handler = next(
        (
            existing
            for existing in root.handlers
            if isinstance(existing, AIOpsLogHandler)
        ),
        None,
    )
    if handler is None:
        handler = AIOpsLogHandler()
        handler.addFilter(ContextFilter())
        root.addHandler(handler)
    else:
        handler.filters.clear()
        handler.addFilter(ContextFilter())
    fmt: logging.Formatter
    if log_format == "json":
        fmt = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s "
            "%(service)s %(trace_id)s %(project_id)s"
        )
    else:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s "
            "(trace=%(trace_id)s project=%(project_id)s): %(message)s"
        )
    handler.setFormatter(fmt)
    root.setLevel(logging.INFO)
