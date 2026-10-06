"""Health endpoint and settings tests."""

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.config import Settings, get_settings
from app.main import create_app


def _client(monkeypatch, *, connected: bool) -> TestClient:
    if connected:
        monkeypatch.setattr(
            "app.routers.health.check_database_connection",
            lambda _db: "ai_attendance",
        )
    else:

        def fail(_db: object) -> str:
            raise OperationalError("SELECT 1", {}, Exception("down"))

        monkeypatch.setattr("app.routers.health.check_database_connection", fail)
    return TestClient(create_app())


def test_health_ok(monkeypatch) -> None:
    with _client(monkeypatch, connected=True) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"]
    assert body["environment"]
    assert body["database"] == "connected"


def test_health_uses_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "Phase Two")
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()

    with _client(monkeypatch, connected=True) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "Phase Two",
        "environment": "test",
        "database": "connected",
    }


def test_health_reports_database_disconnected(monkeypatch) -> None:
    with _client(monkeypatch, connected=False) as client:
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["database"] == "disconnected"
    assert body["app"]
    assert body["environment"]


def test_settings_defaults(monkeypatch) -> None:
    for name in (
        "APP_NAME",
        "APP_ENV",
        "DEBUG",
        "HOST",
        "PORT",
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE",
        "JWT_SECRET",
        "JWT_ALGORITHM",
        "ACCESS_TOKEN_EXPIRE_MINUTES",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "AI Attendance Marker"
    assert settings.app_env == "development"
    assert settings.port == 8000
    assert settings.mysql_host == "localhost"
    assert settings.mysql_port == 3306
    assert settings.mysql_user == "root"
    assert settings.mysql_password.get_secret_value() == ""
    assert settings.mysql_database == "ai_attendance"
    assert settings.jwt_secret.get_secret_value() == ""
    assert settings.jwt_algorithm == "HS256"
    assert settings.access_token_expire_minutes == 60
