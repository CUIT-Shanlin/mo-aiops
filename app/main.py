"""FastAPI app 工厂：lifespan 建 engine/redis + 自动迁移 + 异常处理。"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.agent import rag_router as agent_rag_router
from app.api.agent import router as agent_router
from app.api.audit import router as audit_router
from app.api.alerts import router as alerts_router
from app.api.auth import router as auth_router
from app.api.exports import router as exports_router
from app.api.healing import router as healing_router
from app.api.health import router as health_router
from app.api.k8s import router as k8s_router
from app.api.logs import router as logs_router
from app.api.notifications import router as notifications_router
from app.api.projects import router as projects_router
from app.api.rca import router as rca_router
from app.api.seeds import router as seeds_router
from app.api.settings import router as settings_router
from app.api.topology import router as topology_router
from app.alerts.ingest import IngestConsumer
from app.collectors.scheduler import CollectorScheduler
from app.collectors.windows import MetricWindowStore, TraceCache
from app.core.config import get_settings
from app.core.constants import ErrorCode
from app.core.db import create_engine, make_sessionmaker, ping_db
from app.core.logging import set_trace_id, setup_logging
from app.core.projects import load_projects_config
from app.core.redis import create_redis, ping_redis
from app.db.migrate import run_upgrade_head
from app.observability.metrics import MetricsMiddleware, MetricsRegistry
from app.providers.health import validate_configured_providers
from app.schemas.response import APIError
from app.ws.agent import router as agent_ws_router
from app.ws.alerts import router as alerts_ws_router
from app.ws.healing import router as healing_ws_router
from app.ws.metrics import router as metrics_ws_router
from app.ws.notifications import router as notifications_ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：日志→engine→redis→迁移→探活；关闭：dispose。"""
    settings = get_settings()
    setup_logging(settings.log_format)

    app.state.engine = create_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.redis = create_redis(settings.redis_url, db=settings.redis_db)
    app.state.metric_window_store = MetricWindowStore()
    app.state.trace_cache = TraceCache()

    # 迁移走线程：env.py 在线模式用 asyncio.run() 建临时 loop，
    # 在已运行的 lifespan 事件循环里直接调会抛 RuntimeError。
    await asyncio.to_thread(run_upgrade_head)

    log = logging.getLogger("startup")
    if not await ping_db(app.state.engine):
        log.warning("database ping failed at startup")
    if not await ping_redis(app.state.redis):
        log.warning("redis ping failed at startup")

    app.state.projects_config = load_projects_config()
    app.state.provider_health = {}
    if settings.startup_provider_validation:
        app.state.provider_health = await validate_configured_providers(
            app.state.projects_config
        )
    app.state.collector_scheduler = None
    app.state.collector_task = None
    if settings.startup_collector_enabled:
        app.state.collector_scheduler = CollectorScheduler(
            redis=app.state.redis,
            projects_config=app.state.projects_config,
            metric_window_store=app.state.metric_window_store,
            trace_cache=app.state.trace_cache,
        )
        app.state.collector_task = asyncio.create_task(
            app.state.collector_scheduler.run_forever()
        )
    app.state.ingest_consumer = None
    app.state.ingest_task = None
    if settings.startup_ingest_enabled:
        app.state.ingest_consumer = IngestConsumer(
            redis=app.state.redis,
            sessionmaker=app.state.sessionmaker,
            projects_config=app.state.projects_config,
        )
        app.state.ingest_task = asyncio.create_task(
            app.state.ingest_consumer.run_forever()
        )

    yield

    collector_task = getattr(app.state, "collector_task", None)
    collector_scheduler = getattr(app.state, "collector_scheduler", None)
    if collector_task is not None:
        collector_task.cancel()
        try:
            await collector_task
        except asyncio.CancelledError:
            pass
    if collector_scheduler is not None:
        await collector_scheduler.stop()

    ingest_task = getattr(app.state, "ingest_task", None)
    ingest_consumer = getattr(app.state, "ingest_consumer", None)
    if ingest_consumer is not None:
        await ingest_consumer.stop()
    if ingest_task is not None:
        ingest_task.cancel()
        try:
            await ingest_task
        except asyncio.CancelledError:
            pass

    await app.state.engine.dispose()
    await app.state.redis.aclose()


def get_app() -> FastAPI:
    """app 工厂。"""
    settings = get_settings()
    app = FastAPI(title="mo-chat-aiops", lifespan=lifespan)
    app.state.metrics_registry = MetricsRegistry()

    # 中间件（LIFO：后加先执行）。CORS 先加，trace 后加 → trace 最先跑注入 id。
    # 通配 origin 与 credentials 互斥（CORS 规范）：Starlette 在 "*" 下会反射请求 origin，
    # 叠加 allow_credentials=True 等于放任何站点带凭证跨域。故 origins 为 "*" 时关掉 credentials。
    allow_wildcard = "*" in settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=not allow_wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(MetricsMiddleware, registry=app.state.metrics_registry)

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        """注入 trace_id 到 contextvar 与响应头。"""
        trace_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        set_trace_id(trace_id)
        response = await call_next(request)
        response.headers["X-Request-Id"] = trace_id
        return response

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        """业务异常 → 统一 {code, message, data} body。"""
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message, "data": exc.data},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """请求校验失败 → 统一 body。"""
        return JSONResponse(
            status_code=200,
            content={
                "code": ErrorCode.VALIDATION_ERROR,
                "message": "参数校验失败",
                "data": None,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底未捕获异常 → 50000，不漏 FastAPI 默认 {detail}。"""
        logging.getLogger("unhandled").exception("unhandled exception")
        return JSONResponse(
            status_code=500,
            content={"code": ErrorCode.INTERNAL, "message": "服务器内部错误", "data": None},
        )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(projects_router)
    app.include_router(agent_router)
    app.include_router(agent_rag_router)
    app.include_router(alerts_router)
    app.include_router(healing_router)
    app.include_router(audit_router)
    app.include_router(exports_router)
    app.include_router(rca_router)
    app.include_router(seeds_router)
    app.include_router(topology_router)
    app.include_router(notifications_router)
    app.include_router(logs_router)
    app.include_router(k8s_router)
    app.include_router(settings_router)
    app.include_router(alerts_ws_router)
    app.include_router(agent_ws_router)
    app.include_router(healing_ws_router)
    app.include_router(metrics_ws_router)
    app.include_router(notifications_ws_router)
    return app


app = get_app()
