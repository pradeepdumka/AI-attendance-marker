"""Database package.

`connection.py` owns the MySQL engine, the `SessionLocal` session factory,
and the `get_db` FastAPI dependency. `base.py` re-exports the declarative
base defined in `app.models.base`.

Importing this package does not import the ORM models and does not open
a connection. Import `app.models` when table metadata is needed.
"""

from app.database.connection import (
    SessionLocal,
    check_database_connection,
    dispose_engine,
    get_db,
    get_engine,
    get_session_factory,
)

__all__ = [
    "SessionLocal",
    "check_database_connection",
    "dispose_engine",
    "get_db",
    "get_engine",
    "get_session_factory",
]
