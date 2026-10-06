"""Registration, login, access tokens, and role checks. These tests do not need MySQL."""

import base64
import json
from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # register every mapper before inserting a user
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import (
    AuthConfigurationError,
    TokenError,
    create_access_token,
    decode_access_token,
)
from app.config import Settings, get_settings
from app.database.connection import get_db
from app.main import create_app
from app.models import User, UserRole

TEST_JWT_SECRET = "test-jwt-secret-must-be-32-bytes-or-more"
PASSWORD = "correct-horse-battery"
_USER_FIELDS = {"id", "first_name", "last_name", "email", "role", "is_active"}


@pytest.fixture(autouse=True)
def jwt_test_settings(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def auth_client():
    """App wired to a SQLite users table so login does not open MySQL.

    MySQL's `ON UPDATE CURRENT_TIMESTAMP` default is not valid SQLite.
    The Python-side defaults still fill the timestamp columns. The live
    table's server defaults are restored before the fixture returns.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    columns = list(User.__table__.columns)
    saved_defaults = [(column, column.server_default) for column in columns]
    try:
        for column, _default in saved_defaults:
            column.server_default = None
        User.__table__.create(engine)
    finally:
        for column, default in saved_defaults:
            column.server_default = default
    for column, default in saved_defaults:
        assert column.server_default is default

    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()

    def override_get_db():
        yield session

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, session
    session.close()
    engine.dispose()


def _add_user(
    session,
    *,
    email: str,
    role: UserRole,
    password: str = PASSWORD,
    is_active: bool = True,
) -> User:
    user = User(
        first_name="Ada",
        last_name="Lovelace",
        email=email,
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    assert password not in user.password_hash
    assert len(user.password_hash) <= 255
    return user


def _login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert password not in response.text
    assert "password_hash" not in body
    return body["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _registration(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "first_name": "  Ada ",
        "last_name": " Lovelace ",
        "email": "  Ada@School.edu ",
        "password": PASSWORD,
        "role": "STUDENT",
        "phone": " +1 555-0100 ",
    }
    body.update(overrides)
    return body


def test_password_hash_is_salted_and_not_the_plain_password() -> None:
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)

    assert first != second
    assert first != PASSWORD
    assert PASSWORD not in first
    assert len(first) <= 255
    assert verify_password(PASSWORD, first)
    assert verify_password(PASSWORD, second)
    assert verify_password("wrong-password", first) is False
    assert verify_password(PASSWORD, PASSWORD) is False


def test_access_token_identifies_the_user_and_omits_the_password() -> None:
    token = create_access_token(user_id=7)
    payload = jwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])

    assert decode_access_token(token) == 7
    assert payload["sub"] == "7"
    assert payload["type"] == "access"
    assert "role" not in payload
    assert "password" not in payload
    assert PASSWORD not in token


def test_jwt_secret_must_be_at_least_32_bytes(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "a" * 31)
    get_settings.cache_clear()
    with pytest.raises(AuthConfigurationError) as caught:
        create_access_token(user_id=1)
    assert "a" * 31 not in str(caught.value)

    monkeypatch.setenv("JWT_SECRET", "b" * 32)
    get_settings.cache_clear()
    assert decode_access_token(create_access_token(user_id=4)) == 4


def test_only_hs256_tokens_are_accepted(monkeypatch) -> None:
    monkeypatch.setenv("JWT_ALGORITHM", "none")
    get_settings.cache_clear()
    with pytest.raises(AuthConfigurationError):
        create_access_token(user_id=1)


def test_expired_tampered_and_unsigned_tokens_are_rejected() -> None:
    expired = create_access_token(user_id=1, expires_delta=timedelta(seconds=-30))
    with pytest.raises(TokenError):
        decode_access_token(expired)

    valid = create_access_token(user_id=1)
    head, body, signature = valid.split(".")
    flipped = ("A" if signature[:1] != "A" else "B") + signature[1:]
    with pytest.raises(TokenError):
        decode_access_token(f"{head}.{body}.{flipped}")

    other = jwt.encode(
        {"sub": "1", "type": "access", "iat": 1, "exp": 4_102_444_800},
        "z" * 32,
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        decode_access_token(other)

    unsigned = _unsigned_token({"sub": "1", "type": "access", "iat": 1, "exp": 4_102_444_800})
    with pytest.raises(TokenError):
        decode_access_token(unsigned)


def test_jwt_secret_is_hidden_from_the_settings_repr() -> None:
    settings = Settings(_env_file=None, jwt_secret="x" * 40)

    assert settings.jwt_secret.get_secret_value() == "x" * 40
    assert "xxxx" not in str(settings.jwt_secret)


def test_register_then_login_stores_only_the_password_hash(auth_client) -> None:
    client, session = auth_client

    response = client.post("/auth/register", json=_registration())

    assert response.status_code == 201
    body = response.json()
    assert set(body) == _USER_FIELDS
    assert body == {
        "id": body["id"],
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@school.edu",
        "role": "STUDENT",
        "is_active": True,
    }
    assert PASSWORD not in response.text
    assert "password_hash" not in body

    user = session.get(User, body["id"])
    assert user is not None
    assert user.phone == "+1 555-0100"
    assert user.password_hash != PASSWORD
    assert PASSWORD not in user.password_hash
    assert len(user.password_hash) <= 255
    assert verify_password(PASSWORD, user.password_hash)

    token = _login(client, "ada@school.edu")
    me = client.get("/auth/me", headers=_bearer(token))
    assert me.status_code == 200
    assert me.json()["id"] == user.id
    assert me.json()["email"] == "ada@school.edu"


def test_register_rejects_duplicate_emails(auth_client, monkeypatch) -> None:
    client, session = auth_client
    _add_user(session, email="Ada@School.edu", role=UserRole.TEACHER, is_active=False)

    existing = client.post("/auth/register", json=_registration())
    assert existing.status_code == 409
    assert existing.json() == {"detail": "An account with this email already exists"}
    assert PASSWORD not in existing.text

    teacher = _registration(email="new@school.edu", role="TEACHER")
    del teacher["phone"]
    created = client.post("/auth/register", json=teacher)
    assert created.status_code == 201
    assert created.json()["role"] == "TEACHER"
    stored = session.get(User, created.json()["id"])
    assert stored is not None
    assert stored.phone is None

    monkeypatch.setattr("app.services.auth._find_user_by_email", lambda _db, _email: None)
    raced = client.post("/auth/register", json=teacher)
    assert raced.status_code == 409
    assert raced.json()["detail"] == "An account with this email already exists"
    assert session.get(User, stored.id) is not None


def test_register_validates_the_body(auth_client) -> None:
    client, session = auth_client

    short = client.post("/auth/register", json=_registration(password="short"))
    blank = client.post("/auth/register", json=_registration(password="        "))
    same = client.post(
        "/auth/register",
        json=_registration(email="ada@school.edu", password="ada@school.edu"),
    )
    bad_email = client.post("/auth/register", json=_registration(email="not-an-email"))
    blank_name = client.post("/auth/register", json=_registration(first_name="   "))
    bad_phone = client.post("/auth/register", json=_registration(phone="call-me"))
    missing = client.post("/auth/register", json={})

    assert short.status_code == 422
    assert blank.status_code == 422
    assert same.status_code == 422
    assert bad_email.status_code == 422
    assert blank_name.status_code == 422
    assert bad_phone.status_code == 422
    assert missing.status_code == 422
    assert short.json()["detail"][0]["input"] == "***"
    assert "short" not in short.text
    assert session.get(User, 1) is None


def test_register_accepts_an_admin(auth_client) -> None:
    client, session = auth_client
    created = client.post(
        "/auth/register",
        json=_registration(email="admin@school.edu", role="ADMIN"),
    )
    assert created.status_code == 201
    assert created.json()["role"] == "ADMIN"
    assert "password" not in created.json()
    stored = session.get(User, created.json()["id"])
    assert stored is not None
    assert stored.role == UserRole.ADMIN
    assert stored.password_hash != PASSWORD


def test_login_returns_a_bearer_token_and_me_hides_the_hash(auth_client) -> None:
    client, session = auth_client
    user = _add_user(session, email="Ada@School.edu", role=UserRole.TEACHER)
    stored_hash = user.password_hash

    token = _login(client, "  ada@school.edu  ")
    session.refresh(user)
    assert user.password_hash == stored_hash

    response = client.get("/auth/me", headers={"Authorization": f"bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {
        "id": user.id,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "Ada@School.edu",
        "role": "TEACHER",
        "is_active": True,
    }
    assert set(response.json()) == _USER_FIELDS


def test_login_failures_share_one_401_body(auth_client) -> None:
    client, session = auth_client
    _add_user(session, email="ada@school.edu", role=UserRole.ADMIN)
    _add_user(
        session,
        email="inactive@school.edu",
        role=UserRole.STUDENT,
        is_active=False,
    )

    wrong = client.post(
        "/auth/login",
        json={"email": "ada@school.edu", "password": "not-the-password"},
    )
    unknown = client.post(
        "/auth/login",
        json={"email": "missing@school.edu", "password": PASSWORD},
    )
    inactive = client.post(
        "/auth/login",
        json={"email": "inactive@school.edu", "password": PASSWORD},
    )

    assert wrong.status_code == unknown.status_code == inactive.status_code == 401
    assert wrong.json() == unknown.json() == inactive.json() == {
        "detail": "Invalid email or password",
    }
    assert wrong.headers["www-authenticate"].lower().startswith("bearer")
    assert PASSWORD not in wrong.text
    assert PASSWORD not in inactive.text


def test_plaintext_password_column_does_not_authenticate(auth_client) -> None:
    client, session = auth_client
    user = _add_user(session, email="ada@school.edu", role=UserRole.ADMIN)
    user.password_hash = PASSWORD
    session.commit()

    response = client.post(
        "/auth/login",
        json={"email": "ada@school.edu", "password": PASSWORD},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_login_validates_the_body(auth_client) -> None:
    client, _session = auth_client

    missing = client.post("/auth/login", json={})
    empty_password = client.post(
        "/auth/login",
        json={"email": "ada@school.edu", "password": ""},
    )
    bad_email = client.post(
        "/auth/login",
        json={"email": "not-an-email", "password": PASSWORD},
    )

    assert missing.status_code == 422
    assert empty_password.status_code == 422
    assert bad_email.status_code == 422


def test_protected_routes_reject_missing_and_bad_tokens(auth_client) -> None:
    client, session = auth_client
    user = _add_user(session, email="ada@school.edu", role=UserRole.ADMIN)
    expired = create_access_token(user_id=user.id, expires_delta=timedelta(seconds=-30))

    missing = client.get("/auth/me")
    basic = client.get("/auth/me", headers={"Authorization": "Basic abc"})
    garbage = client.get("/admin/me", headers=_bearer("not-a-token"))
    expired_response = client.get("/teacher/me", headers=_bearer(expired))

    assert missing.status_code == 401
    assert missing.json()["detail"] == "Not authenticated"
    assert basic.status_code == 401
    assert garbage.status_code == 401
    assert garbage.json()["detail"] == "Could not validate credentials"
    assert expired_response.status_code == 401
    assert expired_response.json()["detail"] == "Could not validate credentials"


def test_role_routes_allow_only_the_matching_role(auth_client) -> None:
    client, session = auth_client
    tokens = {}
    for role, email in (
        (UserRole.ADMIN, "admin@school.edu"),
        (UserRole.TEACHER, "teacher@school.edu"),
        (UserRole.STUDENT, "student@school.edu"),
    ):
        _add_user(session, email=email, role=role)
        tokens[role] = _login(client, email)

    gates = (
        ("/admin/me", UserRole.ADMIN, "Admin access required"),
        ("/teacher/me", UserRole.TEACHER, "Teacher access required"),
        ("/student/me", UserRole.STUDENT, "Student access required"),
    )
    for path, allowed, detail in gates:
        allowed_response = client.get(path, headers=_bearer(tokens[allowed]))
        assert allowed_response.status_code == 200
        assert allowed_response.json()["role"] == allowed.value
        assert "password_hash" not in allowed_response.json()

        for role in UserRole:
            if role == allowed:
                continue
            denied = client.get(path, headers=_bearer(tokens[role]))
            assert denied.status_code == 403
            assert denied.json()["detail"] == detail
            assert "www-authenticate" not in denied.headers

        assert client.get(path).status_code == 401


def test_authorization_reads_the_role_from_the_database(auth_client) -> None:
    client, session = auth_client
    user = _add_user(session, email="ada@school.edu", role=UserRole.ADMIN)
    token = _login(client, "ada@school.edu")
    headers = _bearer(token)

    assert client.get("/admin/me", headers=headers).status_code == 200

    user.role = UserRole.STUDENT
    session.commit()

    admin_response = client.get("/admin/me", headers=headers)
    student_response = client.get("/student/me", headers=headers)
    assert admin_response.status_code == 403
    assert student_response.status_code == 200
    assert student_response.json()["role"] == "STUDENT"


def test_inactive_and_unknown_users_lose_access(auth_client) -> None:
    client, session = auth_client
    user = _add_user(session, email="ada@school.edu", role=UserRole.TEACHER)
    token = _login(client, "ada@school.edu")
    headers = _bearer(token)

    user.is_active = False
    session.commit()
    inactive = client.get("/auth/me", headers=headers)
    assert inactive.status_code == 401
    assert inactive.json()["detail"] == "Could not validate credentials"

    user.is_active = True
    session.commit()
    assert client.get("/teacher/me", headers=headers).status_code == 200

    missing = create_access_token(user_id=user.id + 1000)
    assert client.get("/auth/me", headers=_bearer(missing)).status_code == 401


def test_login_without_jwt_secret_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        response = client.post(
            "/auth/login",
            json={"email": "ada@school.edu", "password": PASSWORD},
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "Authentication is not configured"}
    assert PASSWORD not in response.text
    assert TEST_JWT_SECRET not in response.text


def test_health_stays_public_without_a_jwt_secret(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.routers.health.check_database_connection",
        lambda _db: "ai_attendance",
    )

    with TestClient(create_app()) as client:
        health = client.get("/health")
        me = client.get("/auth/me")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert me.status_code == 401


def _unsigned_token(payload: dict) -> str:
    header = _b64({"alg": "none", "typ": "JWT"})
    body = _b64(payload)
    return f"{header}.{body}."


def _b64(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
