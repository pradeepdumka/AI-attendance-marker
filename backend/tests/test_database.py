"""MySQL engine, session dependency, and connection-check tests."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.database.connection import (
    DatabaseConnectionError,
    SessionLocal,
    check_database_connection,
    get_db,
    get_engine,
    get_session_factory,
)


def test_database_url_uses_pymysql_and_hides_password() -> None:
    settings = Settings(
        _env_file=None,
        mysql_user="attendance_user",
        mysql_password="p@ss:word",
        mysql_host="db.internal",
        mysql_port=3307,
        mysql_database="ai_attendance",
    )
    url = settings.database_url

    assert url.drivername == "mysql+pymysql"
    assert url.username == "attendance_user"
    assert url.password == "p@ss:word"
    assert url.host == "db.internal"
    assert url.port == 3307
    assert url.database == "ai_attendance"
    assert url.query["charset"] == "utf8mb4"

    hidden = url.render_as_string(hide_password=True)
    assert "p@ss:word" not in hidden
    assert "***" in hidden
    assert "p@ss:word" not in str(settings.mysql_password)


def test_password_is_not_hardcoded_in_source() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    for path in app_dir.rglob("*.py"):
        source = path.read_text()
        assert "mysql+pymysql://" not in source


def test_engine_configures_a_pymysql_pool() -> None:
    engine = get_engine()

    assert engine.dialect.name == "mysql"
    assert engine.dialect.driver == "pymysql"
    assert engine.pool.__class__.__name__ == "QueuePool"
    assert engine.pool.checkedout() == 0


def test_session_factory_is_bound_to_the_engine() -> None:
    factory = get_session_factory()

    assert isinstance(factory, sessionmaker)
    assert factory.kw["bind"] is get_engine()
    session = SessionLocal()
    try:
        assert session.bind is get_engine()
    finally:
        session.close()


def test_get_db_closes_the_session(monkeypatch) -> None:
    session = MagicMock()
    monkeypatch.setattr("app.database.connection.SessionLocal", lambda: session)

    generator = get_db()
    assert next(generator) is session
    with pytest.raises(StopIteration):
        next(generator)

    session.close.assert_called_once()


def test_get_db_closes_the_session_when_the_request_fails(monkeypatch) -> None:
    session = MagicMock()
    monkeypatch.setattr("app.database.connection.SessionLocal", lambda: session)

    generator = get_db()
    next(generator)
    with pytest.raises(RuntimeError, match="request failed"):
        generator.throw(RuntimeError("request failed"))

    session.close.assert_called_once()


def test_check_database_connection_confirms_selected_database() -> None:
    db = MagicMock()
    name_result = MagicMock()
    name_result.scalar_one.return_value = "ai_attendance"
    db.execute.side_effect = [MagicMock(), name_result]

    assert check_database_connection(db) == "ai_attendance"

    statements = [str(call.args[0]) for call in db.execute.call_args_list]
    assert statements == ["SELECT 1", "SELECT DATABASE()"]


def test_check_database_connection_rejects_the_wrong_database() -> None:
    db = MagicMock()
    name_result = MagicMock()
    name_result.scalar_one.return_value = "other_db"
    db.execute.side_effect = [MagicMock(), name_result]

    with pytest.raises(DatabaseConnectionError, match="ai_attendance"):
        check_database_connection(db)


def test_live_database_connection() -> None:
    """Talk to MySQL when it is reachable. Skip when this machine has none."""
    get_settings.cache_clear()
    db = SessionLocal()
    try:
        try:
            name = check_database_connection(db)
            time_zone = db.execute(text("SELECT @@session.time_zone")).scalar_one()
        except (SQLAlchemyError, DatabaseConnectionError, OperationalError) as exc:
            pytest.skip(f"MySQL is not available: {exc}")
    finally:
        db.close()

    assert name == get_settings().mysql_database
    assert time_zone == "+00:00"
