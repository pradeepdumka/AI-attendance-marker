"""Admin teacher profiles and the signed-in teacher's own profile. These tests do not need MySQL."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.passwords import hash_password, verify_password
from app.config import get_settings
from app.database.connection import get_db
from app.main import create_app
from app.models import AuditLog, Base, SchoolClass, Subject, Teacher, User, UserRole

TEST_JWT_SECRET = "test-jwt-secret-must-be-32-bytes-or-more"
PASSWORD = "correct-horse-battery"
_TEACHER_FIELDS = {
    "teacher_id",
    "employee_id",
    "first_name",
    "last_name",
    "email",
    "phone",
    "department",
    "status",
    "created_at",
    "updated_at",
}


@pytest.fixture(autouse=True)
def jwt_test_settings(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def roster_client():
    """App wired to SQLite so teacher routes do not open MySQL."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    saved_defaults = [
        (column, column.server_default)
        for table in Base.metadata.tables.values()
        for column in table.columns
    ]
    try:
        for column, _default in saved_defaults:
            column.server_default = None
        Base.metadata.create_all(engine)
    finally:
        for column, default in saved_defaults:
            column.server_default = default

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


def _add_user(session, *, email: str, role: UserRole, password: str = PASSWORD) -> User:
    user = User(
        first_name="Admin" if role == UserRole.ADMIN else "Pat",
        last_name="User",
        email=email,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _headers(client, session, *, email: str, role: UserRole) -> dict[str, str]:
    _add_user(session, email=email, role=role)
    return _bearer(_login(client, email))


def _teacher_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "first_name": " Grace ",
        "last_name": " Hopper ",
        "email": " Grace@School.edu ",
        "password": PASSWORD,
        "phone": " +1 555-0199 ",
        "employee_id": " T-014 ",
        "department": " Mathematics ",
        "status": "ACTIVE",
    }
    body.update(overrides)
    return body


def _create(client, headers, **overrides: object):
    return client.post("/admin/teachers", headers=headers, json=_teacher_body(**overrides))


def test_admin_creates_a_teacher_and_stores_only_the_password_hash(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)

    response = _create(client, headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == _TEACHER_FIELDS
    assert body["first_name"] == "Grace"
    assert body["last_name"] == "Hopper"
    assert body["email"] == "grace@school.edu"
    assert body["phone"] == "+1 555-0199"
    assert body["employee_id"] == "T-014"
    assert body["department"] == "Mathematics"
    assert body["status"] == "ACTIVE"
    assert "class_id" not in body
    assert response.headers["location"] == f"/admin/teachers/{body['teacher_id']}"
    assert PASSWORD not in response.text
    assert "password_hash" not in response.text

    session.expire_all()
    teacher = session.get(Teacher, body["teacher_id"])
    assert teacher is not None
    assert teacher.employee_code == "T-014"
    user = session.get(User, teacher.user_id)
    assert user is not None
    assert user.role == UserRole.TEACHER
    assert user.password_hash != PASSWORD
    assert PASSWORD not in user.password_hash
    assert verify_password(PASSWORD, user.password_hash)
    assert session.scalar(select(func.count()).select_from(SchoolClass)) == 0
    assert session.scalar(select(func.count()).select_from(Subject)) == 0

    audit = session.scalars(select(AuditLog)).one()
    assert audit.action == "teacher.created"
    assert audit.entity_type == "teacher"
    assert audit.entity_id == str(teacher.id)
    assert audit.details == {"employee_id": "T-014"}
    assert PASSWORD not in str(audit.details)

    token = _login(client, "grace@school.edu")
    profile = client.get("/teacher/profile", headers=_bearer(token))
    assert profile.status_code == 200
    assert profile.json()["teacher_id"] == teacher.id


def test_create_teacher_rejects_conflicts(roster_client, monkeypatch) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    _add_user(session, email="taken@school.edu", role=UserRole.STUDENT)
    created = _create(client, headers, employee_id="E-014")
    assert created.status_code == 201, created.text

    duplicate_email = _create(client, headers, email="Taken@School.edu", employee_id="E-015")
    duplicate_employee = _create(client, headers, email="other@school.edu", employee_id="E-014")
    same_employee_different_case = _create(
        client,
        headers,
        email="third@school.edu",
        employee_id="e-014",
    )

    assert duplicate_email.status_code == 409
    assert duplicate_email.json()["detail"] == "An account with this email already exists"
    assert duplicate_employee.status_code == 409
    assert duplicate_employee.json()["detail"] == "A teacher with this employee id already exists"
    assert same_employee_different_case.status_code == 409
    assert PASSWORD not in duplicate_email.text

    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Teacher)) == 1

    monkeypatch.setattr("app.services.teachers._email_taken", lambda *_args, **_kwargs: False)
    raced = _create(client, headers, email="grace@school.edu", employee_id="E-099")
    assert raced.status_code == 409
    assert raced.json()["detail"] == "An account with this email already exists"
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Teacher)) == 1


def test_create_teacher_validates_the_body(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)

    short = _create(client, headers, password="short")
    blank_employee = _create(client, headers, employee_id="   ")
    long_department = _create(client, headers, department="D" * 101)
    bad_phone = _create(client, headers, phone="call-me")
    same = _create(client, headers, email="grace@school.edu", password="grace@school.edu")
    missing = client.post("/admin/teachers", headers=headers, json={})

    assert short.status_code == 422
    assert short.json()["detail"][0]["input"] == "***"
    assert "short" not in short.text
    assert blank_employee.status_code == 422
    assert long_department.status_code == 422
    assert bad_phone.status_code == 422
    assert same.status_code == 422
    assert missing.status_code == 422
    assert PASSWORD not in same.text
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Teacher)) == 0


def test_admin_lists_searches_and_pages_teachers(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)

    grace = _create(
        client,
        headers,
        first_name="Grace",
        last_name="Hopper",
        email="grace@navy.edu",
        employee_id="E_1",
        department="Mathematics",
    )
    ada = _create(
        client,
        headers,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@school.edu",
        employee_id="EA1",
        department="Computing",
        phone=None,
    )
    alan = _create(
        client,
        headers,
        first_name="Alan",
        last_name="Turing",
        email="alan@school.edu",
        employee_id="E-100",
        department="Mathematics",
        status="INACTIVE",
    )
    assert grace.status_code == ada.status_code == alan.status_code == 201

    listed = client.get("/admin/teachers", headers=headers, params={"page": 1, "page_size": 2})
    assert listed.status_code == 200, listed.text
    page = listed.json()
    assert page["total"] == 3
    assert page["page"] == 1
    assert page["page_size"] == 2
    assert [item["last_name"] for item in page["items"]] == ["Hopper", "Lovelace"]
    assert "password_hash" not in listed.text
    assert "class_id" not in listed.text

    second = client.get("/admin/teachers", headers=headers, params={"page": 2, "page_size": 2})
    assert [item["last_name"] for item in second.json()["items"]] == ["Turing"]

    by_name = client.get("/admin/teachers", headers=headers, params={"search": "ada love"})
    assert [item["email"] for item in by_name.json()["items"]] == ["ada@school.edu"]
    by_employee = client.get("/admin/teachers", headers=headers, params={"search": "E_1"})
    assert [item["employee_id"] for item in by_employee.json()["items"]] == ["E_1"]
    by_wildcard = client.get("/admin/teachers", headers=headers, params={"search": "%"})
    assert by_wildcard.json()["total"] == 0

    math_active = client.get(
        "/admin/teachers",
        headers=headers,
        params={"status": "ACTIVE", "department": "mathematics"},
    )
    assert [item["email"] for item in math_active.json()["items"]] == ["grace@navy.edu"]
    inactive = client.get("/admin/teachers", headers=headers, params={"status": "INACTIVE"})
    assert [item["email"] for item in inactive.json()["items"]] == ["alan@school.edu"]

    assert client.get("/admin/teachers", headers=headers, params={"page": 0}).status_code == 422
    assert client.get("/admin/teachers", headers=headers, params={"page_size": 101}).status_code == 422


def test_admin_views_updates_and_deactivates_a_teacher(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    created = _create(client, headers)
    assert created.status_code == 201, created.text
    teacher_id = created.json()["teacher_id"]
    url = f"/admin/teachers/{teacher_id}"

    missing = client.get("/admin/teachers/999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Teacher not found"
    assert client.get("/admin/teachers/abc", headers=headers).status_code == 422
    assert client.get("/admin/teachers/0", headers=headers).status_code == 422

    detail = client.get(url, headers=headers)
    assert detail.status_code == 200
    assert detail.json()["employee_id"] == "T-014"
    assert set(detail.json()) == _TEACHER_FIELDS

    updated = client.patch(
        url,
        headers=headers,
        json={
            "last_name": "Murray",
            "phone": None,
            "department": None,
            "employee_id": "T-100",
            "email": "grace.murray@school.edu",
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["last_name"] == "Murray"
    assert body["phone"] is None
    assert body["department"] is None
    assert body["employee_id"] == "T-100"
    assert body["email"] == "grace.murray@school.edu"
    assert PASSWORD not in updated.text
    assert session.scalar(select(func.count()).select_from(SchoolClass)) == 0

    other = _create(client, headers, email="ada@school.edu", employee_id="A-1", department=None)
    assert other.status_code == 201
    duplicate_email = client.patch(url, headers=headers, json={"email": "ada@school.edu"})
    duplicate_employee = client.patch(url, headers=headers, json={"employee_id": "a-1"})
    assert duplicate_email.status_code == 409
    assert duplicate_employee.status_code == 409
    assert client.get(url, headers=headers).json()["email"] == "grace.murray@school.edu"

    password_is_email = client.patch(
        url,
        headers=headers,
        json={"password": "grace.murray@school.edu"},
    )
    assert password_is_email.status_code == 422
    assert password_is_email.json()["detail"] == "Password must not match the email address"
    assert "grace.murray@school.edu" not in password_is_email.text
    assert client.patch(url, headers=headers, json={"employee_id": None}).status_code == 422

    new_password = "a-different-password"
    reset = client.patch(url, headers=headers, json={"password": new_password})
    assert reset.status_code == 200
    assert new_password not in reset.text
    assert _login(client, "grace.murray@school.edu", new_password)

    deactivated = client.delete(url, headers=headers)
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["status"] == "INACTIVE"
    session.expire_all()
    assert session.get(Teacher, teacher_id) is not None
    denied = client.post(
        "/auth/login",
        json={"email": "grace.murray@school.edu", "password": new_password},
    )
    assert denied.status_code == 401

    again = client.delete(url, headers=headers)
    assert again.status_code == 200
    assert again.json()["status"] == "INACTIVE"
    session.expire_all()
    deactivations = session.scalars(
        select(AuditLog).where(AuditLog.action == "teacher.deactivated")
    ).all()
    assert len(deactivations) == 1
    assert new_password not in str(deactivations[0].details)

    restored = client.patch(url, headers=headers, json={"status": "ACTIVE"})
    assert restored.status_code == 200
    assert restored.json()["status"] == "ACTIVE"
    assert _login(client, "grace.murray@school.edu", new_password)


def test_teacher_views_and_updates_only_their_own_profile(roster_client) -> None:
    client, session = roster_client
    admin_headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    grace = _create(client, admin_headers, email="grace@school.edu", employee_id="G-1")
    ada = _create(
        client,
        admin_headers,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@school.edu",
        employee_id="A-1",
        department="Computing",
    )
    assert grace.status_code == ada.status_code == 201

    grace_headers = _bearer(_login(client, "grace@school.edu"))
    profile = client.get("/teacher/profile", headers=grace_headers)
    assert profile.status_code == 200
    assert profile.json()["email"] == "grace@school.edu"
    assert profile.json()["employee_id"] == "G-1"
    assert profile.json()["teacher_id"] != ada.json()["teacher_id"]

    updated = client.patch(
        "/teacher/profile",
        headers=grace_headers,
        json={"last_name": "Murray", "phone": "+1 555-0101", "department": "Computing"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["last_name"] == "Murray"
    assert updated.json()["phone"] == "+1 555-0101"
    assert updated.json()["department"] == "Computing"
    assert updated.json()["employee_id"] == "G-1"
    assert updated.json()["email"] == "grace@school.edu"

    forbidden = client.patch(
        "/teacher/profile",
        headers=grace_headers,
        json={"first_name": "Changed", "employee_id": "G-9", "status": "INACTIVE"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "You cannot change that profile field"
    assert "G-9" not in forbidden.text
    current = client.get("/teacher/profile", headers=grace_headers)
    assert current.json()["first_name"] == "Grace"
    assert current.json()["employee_id"] == "G-1"
    assert current.json()["status"] == "ACTIVE"

    password_change = client.patch(
        "/teacher/profile",
        headers=grace_headers,
        json={"password": "another-real-password"},
    )
    assert password_change.status_code == 403
    assert "another-real-password" not in password_change.text
    assert _login(client, "grace@school.edu")

    cleared = client.patch("/teacher/profile", headers=grace_headers, json={"phone": None})
    assert cleared.status_code == 200
    assert cleared.json()["phone"] is None
    assert client.patch(
        "/teacher/profile",
        headers=grace_headers,
        json={"first_name": None},
    ).status_code == 422

    registered = client.post(
        "/auth/register",
        json={
            "first_name": "Lin",
            "last_name": "Teacher",
            "email": "lin@school.edu",
            "password": PASSWORD,
            "role": "TEACHER",
        },
    )
    assert registered.status_code == 201
    lin_headers = _bearer(_login(client, "lin@school.edu"))
    missing_profile = client.get("/teacher/profile", headers=lin_headers)
    assert missing_profile.status_code == 404
    assert missing_profile.json()["detail"] == "Teacher profile not found"
    missing_update = client.patch(
        "/teacher/profile",
        headers=lin_headers,
        json={"phone": "+1 555-0102"},
    )
    assert missing_update.status_code == 404


def test_teacher_routes_require_the_matching_role(roster_client) -> None:
    client, session = roster_client
    tokens = {}
    for role, email in (
        (UserRole.ADMIN, "admin@school.edu"),
        (UserRole.TEACHER, "teacher@school.edu"),
        (UserRole.STUDENT, "student@school.edu"),
    ):
        _add_user(session, email=email, role=role)
        tokens[role] = _login(client, email)

    created = _create(client, _bearer(tokens[UserRole.ADMIN]), email="grace@school.edu")
    assert created.status_code == 201, created.text
    teacher_id = created.json()["teacher_id"]

    for role in (UserRole.TEACHER, UserRole.STUDENT):
        headers = _bearer(tokens[role])
        assert client.get("/admin/teachers", headers=headers).status_code == 403
        assert client.post("/admin/teachers", headers=headers, json=_teacher_body()).status_code == 403
        assert client.get(f"/admin/teachers/{teacher_id}", headers=headers).status_code == 403
        assert client.patch(
            f"/admin/teachers/{teacher_id}",
            headers=headers,
            json={"last_name": "Nope"},
        ).status_code == 403
        assert client.delete(f"/admin/teachers/{teacher_id}", headers=headers).status_code == 403

    assert client.get("/admin/teachers").status_code == 401
    assert client.get("/teacher/profile").status_code == 401
    assert client.patch("/teacher/profile", json={"phone": "+1 555-0100"}).status_code == 401
    assert client.get("/teacher/profile", headers=_bearer(tokens[UserRole.ADMIN])).status_code == 403
    assert client.get("/teacher/profile", headers=_bearer(tokens[UserRole.STUDENT])).status_code == 403
    assert client.patch(
        "/teacher/profile",
        headers=_bearer(tokens[UserRole.ADMIN]),
        json={"phone": "+1 555-0100"},
    ).status_code == 403

    own = client.get("/teacher/profile", headers=_bearer(tokens[UserRole.TEACHER]))
    assert own.status_code == 404
    assert own.json()["detail"] == "Teacher profile not found"
