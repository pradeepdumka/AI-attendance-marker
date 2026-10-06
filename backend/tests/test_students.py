"""Admin student roster and the signed-in student's profile. These tests do not need MySQL."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.passwords import hash_password, verify_password
from app.config import get_settings
from app.database.connection import get_db
from app.main import create_app
from app.models import (
    AuditLog,
    Base,
    Enrollment,
    EnrollmentStatus,
    FaceEncoding,
    Gender,
    SchoolClass,
    Student,
    User,
    UserRole,
)

TEST_JWT_SECRET = "test-jwt-secret-must-be-32-bytes-or-more"
PASSWORD = "correct-horse-battery"
_STUDENT_FIELDS = {
    "student_id",
    "roll_number",
    "first_name",
    "last_name",
    "email",
    "phone",
    "date_of_birth",
    "gender",
    "class_id",
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
    """App wired to SQLite so roster routes do not open MySQL.

    MySQL server defaults such as `UTC_TIMESTAMP()` are not valid SQLite.
    Python-side defaults still fill those columns. The live defaults are
    restored before the fixture returns.
    """
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


def _class(session, *, name: str = "Grade 10", section: str = "A", active: bool = True) -> SchoolClass:
    school_class = SchoolClass(
        name=name,
        section=section,
        academic_year="2026-2027",
        is_active=active,
    )
    session.add(school_class)
    session.commit()
    session.refresh(school_class)
    return school_class


def _student_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "first_name": " Ada ",
        "last_name": " Lovelace ",
        "email": " Ada@School.edu ",
        "password": PASSWORD,
        "phone": " +1 555-0100 ",
        "roll_number": " 2026-014 ",
        "date_of_birth": "2010-12-10",
        "gender": "FEMALE",
        "status": "ACTIVE",
    }
    body.update(overrides)
    return body


def _create(client, headers, **overrides: object):
    return client.post("/admin/students", headers=headers, json=_student_body(**overrides))


def test_admin_creates_a_student_and_stores_only_the_password_hash(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)

    response = _create(client, headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == _STUDENT_FIELDS
    assert body["first_name"] == "Ada"
    assert body["last_name"] == "Lovelace"
    assert body["email"] == "ada@school.edu"
    assert body["phone"] == "+1 555-0100"
    assert body["roll_number"] == "2026-014"
    assert body["date_of_birth"] == "2010-12-10"
    assert body["gender"] == "FEMALE"
    assert body["class_id"] is None
    assert body["status"] == "ACTIVE"
    assert response.headers["location"] == f"/admin/students/{body['student_id']}"
    assert PASSWORD not in response.text
    assert "password_hash" not in response.text

    session.expire_all()
    student = session.get(Student, body["student_id"])
    assert student is not None
    assert student.gender == Gender.FEMALE
    user = session.get(User, student.user_id)
    assert user is not None
    assert user.role == UserRole.STUDENT
    assert user.password_hash != PASSWORD
    assert PASSWORD not in user.password_hash
    assert verify_password(PASSWORD, user.password_hash)
    assert session.scalar(select(func.count()).select_from(FaceEncoding)) == 0

    audit = session.scalars(select(AuditLog)).one()
    assert audit.action == "student.created"
    assert audit.entity_type == "student"
    assert audit.entity_id == str(student.id)
    assert audit.details == {"roll_number": "2026-014", "class_id": None}
    assert PASSWORD not in str(audit.details)

    token = _login(client, "ada@school.edu")
    profile = client.get("/student/profile", headers=_bearer(token))
    assert profile.status_code == 200
    assert profile.json()["student_id"] == student.id
    assert "password_hash" not in profile.json()


def test_create_student_enrolls_an_active_class(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    school_class = _class(session)

    response = _create(client, headers, email="ada@school.edu", class_id=school_class.id)

    assert response.status_code == 201, response.text
    assert response.json()["class_id"] == school_class.id
    session.expire_all()
    enrollment = session.scalars(select(Enrollment)).one()
    assert enrollment.class_id == school_class.id
    assert enrollment.status == EnrollmentStatus.ACTIVE


def test_create_student_rejects_conflicts(roster_client, monkeypatch) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    _add_user(session, email="taken@school.edu", role=UserRole.TEACHER)
    inactive = _class(session, name="Grade 9", section="B", active=False)
    created = _create(client, headers, roll_number="R-014")
    assert created.status_code == 201, created.text

    duplicate_email = _create(client, headers, email="Taken@School.edu", roll_number="2026-015")
    duplicate_roll = _create(client, headers, email="other@school.edu", roll_number="R-014")
    same_roll_different_case = _create(
        client,
        headers,
        email="third@school.edu",
        roll_number="r-014",
    )
    missing_class = _create(client, headers, email="class@school.edu", roll_number="2026-016", class_id=999)
    inactive_class = _create(
        client,
        headers,
        email="quiet@school.edu",
        roll_number="2026-017",
        class_id=inactive.id,
    )

    assert duplicate_email.status_code == 409
    assert duplicate_email.json()["detail"] == "An account with this email already exists"
    assert duplicate_roll.status_code == 409
    assert duplicate_roll.json()["detail"] == "A student with this roll number already exists"
    assert same_roll_different_case.status_code == 409
    assert missing_class.status_code == 404
    assert missing_class.json()["detail"] == "Class not found"
    assert inactive_class.status_code == 409
    assert inactive_class.json()["detail"] == "Class is not active"
    assert PASSWORD not in duplicate_email.text

    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Student)) == 1

    monkeypatch.setattr("app.services.students._email_taken", lambda *_args, **_kwargs: False)
    raced = _create(client, headers, email="ada@school.edu", roll_number="2026-099")
    assert raced.status_code == 409
    assert raced.json()["detail"] == "An account with this email already exists"
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Student)) == 1


def test_create_student_validates_the_body(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)

    short = _create(client, headers, password="short")
    future = _create(client, headers, date_of_birth="2999-01-01")
    ancient = _create(client, headers, date_of_birth="1800-01-01")
    blank_roll = _create(client, headers, roll_number="   ")
    bad_gender = _create(client, headers, gender="UNKNOWN")
    bad_phone = _create(client, headers, phone="call-me")
    same = _create(client, headers, email="ada@school.edu", password="ada@school.edu")
    missing = client.post("/admin/students", headers=headers, json={})

    assert short.status_code == 422
    assert short.json()["detail"][0]["input"] == "***"
    assert "short" not in short.text
    assert future.status_code == 422
    assert ancient.status_code == 422
    assert blank_roll.status_code == 422
    assert bad_gender.status_code == 422
    assert bad_phone.status_code == 422
    assert same.status_code == 422
    assert missing.status_code == 422
    assert PASSWORD not in same.text
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Student)) == 0


def test_admin_lists_searches_and_pages_students(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    grade_ten = _class(session, name="Grade 10", section="A")
    grade_nine = _class(session, name="Grade 9", section="A")

    ada = _create(
        client,
        headers,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@school.edu",
        roll_number="R_1",
        class_id=grade_ten.id,
        gender="FEMALE",
    )
    grace = _create(
        client,
        headers,
        first_name="Grace",
        last_name="Hopper",
        email="grace@navy.edu",
        roll_number="RA1",
        class_id=grade_nine.id,
        gender="FEMALE",
        phone=None,
    )
    alan = _create(
        client,
        headers,
        first_name="Alan",
        last_name="Turing",
        email="alan@school.edu",
        roll_number="R-100",
        gender="MALE",
        status="INACTIVE",
    )
    assert ada.status_code == grace.status_code == alan.status_code == 201

    listed = client.get("/admin/students", headers=headers, params={"page": 1, "page_size": 2})
    assert listed.status_code == 200, listed.text
    page = listed.json()
    assert page["total"] == 3
    assert page["page"] == 1
    assert page["page_size"] == 2
    assert [item["last_name"] for item in page["items"]] == ["Hopper", "Lovelace"]
    assert "password_hash" not in listed.text

    second = client.get("/admin/students", headers=headers, params={"page": 2, "page_size": 2})
    assert [item["last_name"] for item in second.json()["items"]] == ["Turing"]
    beyond = client.get("/admin/students", headers=headers, params={"page": 9, "page_size": 2})
    assert beyond.json()["items"] == []
    assert beyond.json()["total"] == 3

    by_name = client.get("/admin/students", headers=headers, params={"search": "ada love"})
    assert [item["email"] for item in by_name.json()["items"]] == ["ada@school.edu"]
    by_email = client.get("/admin/students", headers=headers, params={"search": "NAVY.EDU"})
    assert [item["email"] for item in by_email.json()["items"]] == ["grace@navy.edu"]
    by_roll = client.get("/admin/students", headers=headers, params={"search": "R_1"})
    assert [item["roll_number"] for item in by_roll.json()["items"]] == ["R_1"]
    by_wildcard = client.get("/admin/students", headers=headers, params={"search": "%"})
    assert by_wildcard.json()["total"] == 0

    active_in_ten = client.get(
        "/admin/students",
        headers=headers,
        params={"status": "ACTIVE", "class_id": grade_ten.id, "gender": "FEMALE"},
    )
    assert [item["email"] for item in active_in_ten.json()["items"]] == ["ada@school.edu"]
    inactive = client.get("/admin/students", headers=headers, params={"status": "INACTIVE"})
    assert [item["email"] for item in inactive.json()["items"]] == ["alan@school.edu"]

    assert client.get("/admin/students", headers=headers, params={"page": 0}).status_code == 422
    assert client.get("/admin/students", headers=headers, params={"page_size": 101}).status_code == 422
    assert client.get("/admin/students", headers=headers, params={"status": "GONE"}).status_code == 422


def test_admin_views_updates_and_deactivates_a_student(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    first = _class(session, name="Grade 10", section="A")
    second = _class(session, name="Grade 11", section="B")
    created = _create(client, headers, class_id=first.id)
    assert created.status_code == 201, created.text
    student_id = created.json()["student_id"]
    url = f"/admin/students/{student_id}"

    missing = client.get("/admin/students/999", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Student not found"
    assert client.get("/admin/students/abc", headers=headers).status_code == 422
    assert client.get("/admin/students/0", headers=headers).status_code == 422

    detail = client.get(url, headers=headers)
    assert detail.status_code == 200
    assert detail.json()["class_id"] == first.id
    assert set(detail.json()) == _STUDENT_FIELDS

    conflict = client.patch(url, headers=headers, json={"last_name": "Byron", "class_id": 999})
    assert conflict.status_code == 404
    assert client.get(url, headers=headers).json()["last_name"] == "Lovelace"

    updated = client.patch(
        url,
        headers=headers,
        json={
            "last_name": "Byron",
            "phone": None,
            "gender": "OTHER",
            "date_of_birth": None,
            "roll_number": "2026-100",
            "class_id": second.id,
            "email": "ada.byron@school.edu",
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["last_name"] == "Byron"
    assert body["phone"] is None
    assert body["gender"] == "OTHER"
    assert body["date_of_birth"] is None
    assert body["roll_number"] == "2026-100"
    assert body["email"] == "ada.byron@school.edu"
    assert body["class_id"] == second.id
    assert PASSWORD not in updated.text

    session.expire_all()
    enrollments = session.scalars(select(Enrollment).order_by(Enrollment.class_id)).all()
    by_class = {enrollment.class_id: enrollment.status for enrollment in enrollments}
    assert by_class[first.id] == EnrollmentStatus.WITHDRAWN
    assert by_class[second.id] == EnrollmentStatus.ACTIVE

    cleared = client.patch(url, headers=headers, json={"class_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["class_id"] is None

    other = _create(client, headers, email="grace@school.edu", roll_number="G-1")
    assert other.status_code == 201
    duplicate_email = client.patch(
        url,
        headers=headers,
        json={"email": "grace@school.edu"},
    )
    duplicate_roll = client.patch(url, headers=headers, json={"roll_number": "g-1"})
    assert duplicate_email.status_code == 409
    assert duplicate_roll.status_code == 409

    password_is_email = client.patch(
        url,
        headers=headers,
        json={"password": "ada.byron@school.edu"},
    )
    assert password_is_email.status_code == 422
    assert password_is_email.json()["detail"] == "Password must not match the email address"
    assert "ada.byron@school.edu" not in password_is_email.text
    null_name = client.patch(url, headers=headers, json={"first_name": None})
    assert null_name.status_code == 422

    new_password = "a-different-password"
    reset = client.patch(url, headers=headers, json={"password": new_password})
    assert reset.status_code == 200
    assert new_password not in reset.text
    assert _login(client, "ada.byron@school.edu", new_password)

    deactivated = client.delete(url, headers=headers)
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["status"] == "INACTIVE"
    session.expire_all()
    assert session.get(Student, student_id) is not None
    denied = client.post(
        "/auth/login",
        json={"email": "ada.byron@school.edu", "password": new_password},
    )
    assert denied.status_code == 401

    again = client.delete(url, headers=headers)
    assert again.status_code == 200
    assert again.json()["status"] == "INACTIVE"
    session.expire_all()
    deactivations = session.scalars(
        select(AuditLog).where(AuditLog.action == "student.deactivated")
    ).all()
    assert len(deactivations) == 1
    assert PASSWORD not in str(deactivations[0].details)
    assert new_password not in str(deactivations[0].details)

    restored = client.patch(url, headers=headers, json={"status": "ACTIVE"})
    assert restored.status_code == 200
    assert restored.json()["status"] == "ACTIVE"
    assert _login(client, "ada.byron@school.edu", new_password)


def test_student_views_only_their_own_profile(roster_client) -> None:
    client, session = roster_client
    admin_headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    ada = _create(client, admin_headers, email="ada@school.edu", roll_number="A-1")
    grace = _create(
        client,
        admin_headers,
        first_name="Grace",
        last_name="Hopper",
        email="grace@school.edu",
        roll_number="G-1",
    )
    assert ada.status_code == grace.status_code == 201

    ada_headers = _bearer(_login(client, "ada@school.edu"))
    profile = client.get("/student/profile", headers=ada_headers)
    assert profile.status_code == 200
    assert profile.json()["email"] == "ada@school.edu"
    assert profile.json()["roll_number"] == "A-1"
    assert profile.json()["student_id"] != grace.json()["student_id"]

    registered = client.post(
        "/auth/register",
        json={
            "first_name": "Lin",
            "last_name": "Student",
            "email": "lin@school.edu",
            "password": PASSWORD,
            "role": "STUDENT",
        },
    )
    assert registered.status_code == 201
    lin_headers = _bearer(_login(client, "lin@school.edu"))
    missing_profile = client.get("/student/profile", headers=lin_headers)
    assert missing_profile.status_code == 404
    assert missing_profile.json()["detail"] == "Student profile not found"


def test_student_routes_require_the_matching_role(roster_client) -> None:
    client, session = roster_client
    tokens = {}
    for role, email in (
        (UserRole.ADMIN, "admin@school.edu"),
        (UserRole.TEACHER, "teacher@school.edu"),
        (UserRole.STUDENT, "student@school.edu"),
    ):
        _add_user(session, email=email, role=role)
        tokens[role] = _login(client, email)

    created = _create(client, _bearer(tokens[UserRole.ADMIN]), email="ada@school.edu")
    assert created.status_code == 201, created.text
    student_id = created.json()["student_id"]

    for role in (UserRole.TEACHER, UserRole.STUDENT):
        headers = _bearer(tokens[role])
        assert client.get("/admin/students", headers=headers).status_code == 403
        assert client.post("/admin/students", headers=headers, json=_student_body()).status_code == 403
        assert client.get(f"/admin/students/{student_id}", headers=headers).status_code == 403
        assert client.patch(
            f"/admin/students/{student_id}",
            headers=headers,
            json={"last_name": "Nope"},
        ).status_code == 403
        assert client.delete(f"/admin/students/{student_id}", headers=headers).status_code == 403

    assert client.get("/admin/students").status_code == 401
    assert client.get("/student/profile").status_code == 401
    assert client.get("/student/profile", headers=_bearer(tokens[UserRole.ADMIN])).status_code == 403
    assert client.get("/student/profile", headers=_bearer(tokens[UserRole.TEACHER])).status_code == 403

    own = client.get("/student/profile", headers=_bearer(tokens[UserRole.STUDENT]))
    assert own.status_code == 404
    assert own.json()["detail"] == "Student profile not found"
