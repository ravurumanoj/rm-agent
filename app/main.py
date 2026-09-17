from contextlib import asynccontextmanager
from datetime import datetime
import logging
import sys
import asyncio
import contextlib
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
                logger.exception("SSE listener task crashed", exc_info=exc)

    net = configure_network_environment()
    configure_observability()
    logger.info(
        "Network settings applied",
        extra={
            "has_http_proxy": net["has_http_proxy"],
            "has_https_proxy": net["has_https_proxy"],
            "has_no_proxy": net["has_no_proxy"],
            "has_custom_ca_bundle": net["has_custom_ca_bundle"],
            "ssl_verify": net["ssl_verify"],
        },
    )

    missing = settings.missing_runtime_settings()
    if missing["provider"]:
        logger.warning(
            "Missing required provider settings for LLM_PROVIDER=%s: %s",
            settings.LLM_PROVIDER,
            ", ".join(missing["provider"]),
        )
    if missing["mcp"]:
        logger.warning("MCP is enabled but configuration is incomplete: %s", ", ".join(missing["mcp"]))
    if missing["phoenix"]:
        logger.warning(
            "Phoenix observability is enabled but configuration is incomplete: %s",
            ", ".join(missing["phoenix"]),
        )

    try:
        init_db()
        if settings.POSTGRES_ENABLED:
            logger.info("PostgreSQL database initialised.")
        else:
            logger.info("PostgreSQL disabled; startup DB init skipped.")
    except Exception as exc:
        if settings.DB_STARTUP_REQUIRED:
            logger.critical("PostgreSQL database init failed and DB_STARTUP_REQUIRED=true: %s", exc)
            raise RuntimeError("Database startup dependency failed") from exc
        logger.error("PostgreSQL database init failed; starting in degraded mode: %s", exc)

    if getattr(settings, "SSE_ENABLED", False):
        webhook_url = getattr(settings, "SSE_WEBHOOK_URL", "").strip()
        if webhook_url:
            logger.info("Starting SSE listener task -> %s", webhook_url)
            sse_task = asyncio.create_task(
                start_sse_listener(
                    webhook_url,
                    getattr(settings, "SSE_MAX_CONCURRENT", 10),
                ),
                name="sse-listener",
            )
            sse_task.add_done_callback(_log_sse_task_result)
        else:
            logger.warning("SSE listener enabled but SSE_WEBHOOK_URL is empty")
    else:
        logger.info("SSE listener disabled (SSE_ENABLED is not true)")

    yield

    if sse_task is not None:
        sse_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sse_task
        logger.info("SSE listener stopped")

    shutdown_observability()


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
        correlation_id = str(uuid4())
        request.state.correlation_id = correlation_id

        token = set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
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
    async def global_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled exception: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal error occurred. Please try again later."},
        )

    return app
