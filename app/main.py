from contextlib import asynccontextmanager
from datetime import datetime
import logging
import sys
import asyncio
import contextlib
import time
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.constants import (
    CORRELATION_ID_HEADER,
    DOCS_URL,
    HEALTH_STATUS_DEGRADED,
    HEALTH_STATUS_HEALTHY,
    OPENAPI_URL,
    REDOC_URL,
)
from app.db.session import init_db, ping_db
from app.routes.agent import router as agent_router
from app.routes.webhook import router as webhook_router
from app.services.network import configure_network_environment
from app.services.observability import configure_observability, shutdown_observability
from app.services.sse_listener import start_sse_listener
from app.utils.logger import configure_logging, logger, reset_correlation_id, set_correlation_id


@asynccontextmanager
async def _lifespan(app: FastAPI):
    sse_task: asyncio.Task | None = None

    def _log_sse_task_result(task: asyncio.Task) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                logger.error("[STARTUP] sse_listener_crashed error=%s: %s", type(exc).__name__, exc, exc_info=exc)

    net = configure_network_environment()
    configure_observability()
    logger.info(
        "[STARTUP] configuration_loaded",
        extra={
            **net,
            "app_name": settings.APP_NAME,
            "version": settings.VERSION,
            "debug": settings.DEBUG,
            "reload": settings.RELOAD,
            "host": settings.HOST,
            "port": settings.PORT,
            "log_level": settings.LOG_LEVEL,
            "llm_provider": settings.LLM_PROVIDER,
            "llm_model": settings.default_model_for(),
            "postgres_enabled": settings.POSTGRES_ENABLED,
            "db_startup_required": settings.DB_STARTUP_REQUIRED,
            "sse_enabled": settings.SSE_ENABLED,
            "has_sse_webhook_url": bool(settings.SSE_WEBHOOK_URL.strip()),
            "phoenix_enabled": settings.PHOENIX_ENABLED,
            "phoenix_local_mode": settings.PHOENIX_LOCAL_MODE,
            "enable_file_logging": settings.ENABLE_FILE_LOGGING,
            "log_file": settings.LOG_FILE,
        },
    )
    missing = {group: keys for group, keys in settings.missing_runtime_settings().items() if keys}
    if missing:
        logger.warning(
            "[STARTUP] settings_incomplete llm_provider=%s missing=%s; affected features will fail or stay off",
            settings.LLM_PROVIDER, missing,
        )

    try:
        init_db()
        logger.info("[STARTUP] database %s", "initialised" if settings.POSTGRES_ENABLED else "disabled (POSTGRES_ENABLED=false)")
    except Exception as exc:
        if settings.DB_STARTUP_REQUIRED:
            logger.critical("[STARTUP] database_init_failed required=true error=%s; refusing to start", exc)
            raise RuntimeError("Database startup dependency failed") from exc
        logger.error("[STARTUP] database_init_failed required=false error=%s; starting in degraded mode", exc)

    if settings.SSE_ENABLED:
        webhook_url = settings.SSE_WEBHOOK_URL.strip()
        if webhook_url:
            logger.info("[STARTUP] sse_listener_starting webhook_url=%s", webhook_url)
            sse_task = asyncio.create_task(
                start_sse_listener(webhook_url, settings.SSE_MAX_CONCURRENT),
                name="sse-listener",
            )
            sse_task.add_done_callback(_log_sse_task_result)
        else:
            logger.warning("[STARTUP] sse_listener_not_started reason=SSE_WEBHOOK_URL_empty")
    else:
        logger.info("[STARTUP] sse_listener_disabled")

    try:
        yield
    finally:
        if sse_task is not None:
            sse_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sse_task
        shutdown_observability()
        logger.info("[SHUTDOWN] completed sse_listener_stopped=%s spans_flushed=true", sse_task is not None)


def _validate_cors(origins: list[str]) -> list[str]:
    normalized = [origin.strip() for origin in origins if origin.strip()]
    if "*" in normalized:
        raise ValueError(
            "Invalid CORS configuration: BACKEND_CORS_ORIGINS cannot include '*' when credentials are enabled."
        )
    return normalized


def create_app() -> FastAPI:
    """Application factory for the RM agentic assistant."""
    configure_logging(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    cors_origins = _validate_cors(settings.BACKEND_CORS_ORIGINS)

    app = FastAPI(
        title=settings.APP_NAME,
        description=settings.DESCRIPTION,
        version=settings.VERSION,
        debug=settings.DEBUG,
        docs_url=DOCS_URL,
        redoc_url=REDOC_URL,
        openapi_url=OPENAPI_URL,
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        # Keep an upstream id so one query can be followed across systems; otherwise start a new one.
        incoming = request.headers.get(CORRELATION_ID_HEADER, "").strip()
        correlation_id = incoming if 0 < len(incoming) <= 128 else str(uuid4())
        request.state.correlation_id = correlation_id

        token = set_correlation_id(correlation_id)
        started = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
        finally:
            # One access line per request, correlated (uvicorn's own access log carries no correlation id).
            status_code = response.status_code if response is not None else 500
            (logger.debug if request.url.path == "/health" else logger.info if status_code < 500 else logger.error)(
                "[HTTP] request method=%s path=%s status=%s duration_ms=%s client=%s",
                request.method, request.url.path, status_code, int((time.perf_counter() - started) * 1000),
                request.client.host if request.client else "-",
            )
            reset_correlation_id(token)

        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response

    @app.get("/health", tags=["System"], status_code=status.HTTP_200_OK)
    async def health_check():
        """App liveness probe; dependency checks are handled at usage points."""

        if settings.POSTGRES_ENABLED:
            db_ok = ping_db()
            db_state = "ok" if db_ok else "unreachable"
            app_status = HEALTH_STATUS_HEALTHY if db_ok else HEALTH_STATUS_DEGRADED
        else:
            db_state = "disabled"
            app_status = HEALTH_STATUS_HEALTHY

        return {
            "status": app_status,
            "timestamp": datetime.now().isoformat(),
            "version": settings.VERSION,
            "checks": {
                "api": "ok",
                "postgresql": db_state,
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            },
        }

    app.include_router(agent_router)
    app.include_router(webhook_router)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "[API] unhandled_exception method=%s path=%s error=%s: %s",
            request.method, request.url.path, type(exc).__name__, exc, exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal error occurred. Please try again later."},
        )

    return app
