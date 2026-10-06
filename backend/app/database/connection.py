"""MySQL engine, session factory, and the FastAPI session dependency.

The engine owns a pool of PyMySQL connections. A session is a short-lived
unit of work that borrows one of those connections. `get_db` opens that
session for a single request and returns the connection to the pool when
the request finishes.

Importing this module does not open a TCP connection. The pool connects
on the first statement.
"""

from collections.abc import Generator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


class DatabaseConnectionError(Exception):
    """Raised when a connectivity check cannot use the configured database."""


def get_engine() -> Engine:
    """Create the process-wide engine on first use and reuse it afterward.

    `create_engine` configures the pool. It does not open a connection.
    The first session that runs a statement checks one out.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=3600,
            pool_pre_ping=True,
            # CURRENT_TIMESTAMP follows the session time zone. Pin it to UTC
            # so MySQL ON UPDATE defaults match the UTC values the ORM writes.
            # UTC_TIMESTAMP() is already UTC either way.
            connect_args={
                "connect_timeout": 5,
                "init_command": "SET time_zone = '+00:00'",
            },
            echo=False,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the session factory bound to the shared engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


def SessionLocal() -> Session:
    """Open one ORM session from the shared session factory."""
    return get_session_factory()()


def get_db() -> Generator[Session, None, None]:
    """Yield a request-scoped session, then close it.

    FastAPI runs this dependency once per request. The yielded session is
    how that request reads and writes. The `finally` block runs after the
    response is produced, including when the route raises, so the borrowed
    connection always returns to the pool.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection(db: Session) -> str:
    """Run a trivial query and return the selected database name.

    The selected schema must be the configured database (`ai_attendance`
    unless `MYSQL_DATABASE` overrides it).
    """
    settings = get_settings()
    db.execute(text("SELECT 1"))
    current = db.execute(text("SELECT DATABASE()")).scalar_one()
    expected = settings.mysql_database
    if current != expected:
        raise DatabaseConnectionError(
            f"Connected to database {current!r}; expected {expected!r}."
        )
    return str(current)


def dispose_engine() -> None:
    """Close every pooled connection and drop the cached engine."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
