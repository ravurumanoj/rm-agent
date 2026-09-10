from urllib.parse import quote_plus
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import settings
from app.utils.logger import logger

Base = declarative_base()


def _connection_string() -> str:
    password = quote_plus(settings.POSTGRES_PASSWORD)
    return (
        f"postgresql+psycopg://{settings.POSTGRES_USER}:{password}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DATABASE}"
    )


engine = (
    create_engine(
        _connection_string(),
        pool_pre_ping=True,
        echo=settings.DB_ECHO,
        connect_args={"connect_timeout": settings.DB_CONNECT_TIMEOUT_SECONDS},
    )
    if settings.POSTGRES_ENABLED
    else None
)
SessionLocal = (
    sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False) if engine is not None else None
)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transaction scope that always closes the session."""
    if SessionLocal is None:
        raise RuntimeError("Postgres is disabled (POSTGRES_ENABLED=false).")

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session | None]:
    """FastAPI dependency that yields one DB session per request."""
    if SessionLocal is None:
        yield None
        return

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables if missing. Called once on app startup."""
    if engine is None:
        logger.info("PostgreSQL disabled via POSTGRES_ENABLED=false; skipping table init.")
        return

    from app.db import models  # noqa: F401  (ensure models are registered)

    Base.metadata.create_all(bind=engine)


def ping_db() -> bool:
    """Cheap connectivity check used by the /health endpoint."""
    if engine is None:
        return False

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
