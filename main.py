import argparse
import os
import uvicorn

from app.utils.logger import logger


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run rm-agent FastAPI server.")
    parser.add_argument("--env-file", help="Path to .env file to load at startup.")
    parser.add_argument("--host", help="Host interface to bind.")
    parser.add_argument("--port", type=int, help="Port to bind.")
    parser.add_argument("--log-level", choices=["critical", "error", "warning", "info", "debug", "trace"], help="Uvicorn log level.")
    parser.add_argument("--debug", action="store_true", help="Enable FastAPI debug mode for this run.")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload.")
    parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload.")
    return parser.parse_args()


def _build_app():
    from app.main import create_app

    return create_app()


def _load_settings():
    from app.config import settings

    return settings


app = _build_app() if __name__ != "__main__" else None


def run_server() -> None:
    args = _parse_args()

    if args.env_file:
        os.environ["APP_ENV_FILE"] = args.env_file
    if args.debug:
        os.environ["DEBUG"] = "true"

    app_instance = _build_app()
    settings = _load_settings()

    host = args.host or settings.HOST
    port = args.port or settings.PORT
    log_level = args.log_level or settings.LOG_LEVEL.lower()

    reload_enabled = settings.RELOAD
    if args.reload:
        reload_enabled = True
    if args.no_reload:
        reload_enabled = False

    logger.info("Starting server on %s:%s", host, port)

    app_target: object = app_instance
    if reload_enabled:
        app_target = "main:app"

    uvicorn.run(
        app_target,
        host=host,
        port=port,
        reload=reload_enabled,
        log_level=log_level,
    )

if __name__ == "__main__":
    run_server()
