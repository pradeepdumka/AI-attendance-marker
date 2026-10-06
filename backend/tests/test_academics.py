"""Classes, subjects, teacher assignment, and student enrollment. These tests do not need MySQL."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.passwords import hash_password
from app.config import get_settings
from app.database.connection import get_db
from app.main import create_app
from app.models import (
    AuditLog,
    Base,
    Enrollment,
    EnrollmentStatus,
    SchoolClass,
    Student,
    Subject,
    Teacher,
    User,
    UserRole,
)

TEST_JWT_SECRET = "test-jwt-secret-must-be-32-bytes-or-more"
PASSWORD = "correct-horse-battery"
_CLASS_FIELDS = {
    "class_id",
    "name",
    "section",
    "academic_year",
    "class_teacher_id",
    "status",
    "created_at",
    "updated_at",
}
_SUBJECT_FIELDS = {
    "subject_id",
    "class_id",
    "name",
    "code",
    "teacher_id",
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
    """App wired to SQLite so academic routes do not open MySQL."""
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


def _user(session, *, email: str, role: UserRole, active: bool = True) -> User:
    user = User(
        first_name="Ada" if role != UserRole.ADMIN else "Admin",
        last_name="User",
        email=email,
        password_hash=hash_password(PASSWORD),
        role=role,
        is_active=active,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _teacher(session, *, email: str, employee_id: str, active: bool = True) -> Teacher:
    user = _user(session, email=email, role=UserRole.TEACHER, active=active)
    teacher = Teacher(user_id=user.id, employee_code=employee_id, department="Mathematics")
    session.add(teacher)
    session.commit()
    session.refresh(teacher)
    return teacher


def _student(session, *, email: str, roll_number: str, active: bool = True) -> Student:
    user = _user(session, email=email, role=UserRole.STUDENT, active=active)
    student = Student(user_id=user.id, roll_number=roll_number)
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _admin(client, session) -> dict[str, str]:
    _user(session, email="admin@school.edu", role=UserRole.ADMIN)
    return _login(client, "admin@school.edu")


def _class_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": " Grade 10 ",
        "section": " A ",
        "academic_year": " 2026-2027 ",
    }
    body.update(overrides)
    return body


def test_admin_creates_lists_updates_and_deactivates_classes(roster_client) -> None:
    client, session = roster_client
    headers = _admin(client, session)

    created = client.post("/admin/classes", headers=headers, json=_class_body())
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == _CLASS_FIELDS
    assert body["name"] == "Grade 10"
    assert body["section"] == "A"
    assert body["academic_year"] == "2026-2027"
    assert body["class_teacher_id"] is None
    assert body["status"] == "ACTIVE"
    assert created.headers["location"] == f"/admin/classes/{body['class_id']}"

    duplicate = client.post(
        "/admin/classes",
        headers=headers,
        json=_class_body(name="grade 10", section="a"),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == (
        "A class with this name, section, and academic year already exists"
    )
    other = client.post(
        "/admin/classes",
        headers=headers,
        json=_class_body(name="Grade 9", section="B"),
    )
    assert other.status_code == 201, other.text

    listed = client.get("/admin/classes", headers=headers, params={"search": "grade 10 a", "page_size": 1})
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["name"] == "Grade 10"
    assert client.get("/admin/classes", headers=headers, params={"search": "%"}).json()["total"] == 0
    assert client.get(
        "/admin/classes",
        headers=headers,
        params={"academic_year": "2026-2027", "status": "ACTIVE"},
    ).json()["total"] == 2
    assert client.get("/admin/classes", headers=headers, params={"page": 0}).status_code == 422
    assert client.post("/admin/classes", headers=headers, json={"name": "   "}).status_code == 422

    class_id = body["class_id"]
    updated = client.patch(
        f"/admin/classes/{class_id}",
        headers=headers,
        json={"section": "C"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["section"] == "C"
    assert client.get(f"/admin/classes/{class_id}", headers=headers).status_code == 200
    assert client.get("/admin/classes/999", headers=headers).status_code == 404

    deactivated = client.delete(f"/admin/classes/{class_id}", headers=headers)
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "INACTIVE"
    again = client.delete(f"/admin/classes/{class_id}", headers=headers)
    assert again.status_code == 200
    session.expire_all()
    assert session.get(SchoolClass, class_id) is not None
    assert session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "class.deactivated")
    ) == 1


def test_admin_creates_lists_and_deactivates_subjects(roster_client) -> None:
    client, session = roster_client
    headers = _admin(client, session)
    active = client.post("/admin/classes", headers=headers, json=_class_body())
    inactive = client.post(
        "/admin/classes",
        headers=headers,
        json=_class_body(name="Grade 9", section="B", status="INACTIVE"),
    )
    other = client.post(
        "/admin/classes",
        headers=headers,
        json=_class_body(name="Grade 11", section="A"),
    )
    assert active.status_code == inactive.status_code == other.status_code == 201
    class_id = active.json()["class_id"]

    created = client.post(
        "/admin/subjects",
        headers=headers,
        json={"class_id": class_id, "name": " Mathematics ", "code": " MATH "},
    )
    assert created.status_code == 201, created.text
    subject = created.json()
    assert set(subject) == _SUBJECT_FIELDS
    assert subject["name"] == "Mathematics"
    assert subject["code"] == "MATH"
    assert subject["teacher_id"] is None
    assert subject["class_id"] == class_id
    assert created.headers["location"] == f"/admin/subjects/{subject['subject_id']}"

    duplicate = client.post(
        "/admin/subjects",
        headers=headers,
        json={"class_id": class_id, "name": "Maths", "code": "math"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "A subject with this code already exists in the class"
    closed = client.post(
        "/admin/subjects",
        headers=headers,
        json={"class_id": inactive.json()["class_id"], "name": "Science", "code": "SCI"},
    )
    assert closed.status_code == 409
    assert closed.json()["detail"] == "Class is not active"
    same_code_other_class = client.post(
        "/admin/subjects",
        headers=headers,
        json={"class_id": other.json()["class_id"], "name": "Mathematics", "code": "MATH"},
    )
    assert same_code_other_class.status_code == 201, same_code_other_class.text

    listed = client.get(
        "/admin/subjects",
        headers=headers,
        params={"search": "math", "class_id": class_id, "status": "ACTIVE"},
    )
    assert [item["code"] for item in listed.json()["items"]] == ["MATH"]
    assert client.get("/admin/subjects/999", headers=headers).status_code == 404

    updated = client.patch(
        f"/admin/subjects/{subject['subject_id']}",
        headers=headers,
        json={"name": "Advanced Mathematics"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Advanced Mathematics"

    deactivated = client.delete(f"/admin/subjects/{subject['subject_id']}", headers=headers)
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "INACTIVE"
    session.expire_all()
    assert session.get(Subject, subject["subject_id"]) is not None


def test_teacher_assignment_replaces_one_slot(roster_client) -> None:
    client, session = roster_client
    headers = _admin(client, session)
    grace = _teacher(session, email="grace@school.edu", employee_id="T-1")
    ada = _teacher(session, email="ada@school.edu", employee_id="T-2")
    inactive = _teacher(session, email="old@school.edu", employee_id="T-9", active=False)
    created = client.post("/admin/classes", headers=headers, json=_class_body())
    assert created.status_code == 201, created.text
    class_id = created.json()["class_id"]
    subject = client.post(
        "/admin/subjects",
        headers=headers,
        json={"class_id": class_id, "name": "Mathematics", "code": "MATH"},
    )
    assert subject.status_code == 201, subject.text
    subject_id = subject.json()["subject_id"]

    missing = client.put(
        f"/admin/classes/{class_id}/teacher",
        headers=headers,
        json={"teacher_id": 999},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Teacher not found"
    blocked = client.put(
        f"/admin/classes/{class_id}/teacher",
        headers=headers,
        json={"teacher_id": inactive.id},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Teacher is not active"

    assigned = client.put(
        f"/admin/classes/{class_id}/teacher",
        headers=headers,
        json={"teacher_id": grace.id},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["class_teacher_id"] == grace.id
    again = client.put(
        f"/admin/classes/{class_id}/teacher",
        headers=headers,
        json={"teacher_id": grace.id},
    )
    assert again.status_code == 200
    assert again.json()["class_teacher_id"] == grace.id
    replaced = client.put(
        f"/admin/classes/{class_id}/teacher",
        headers=headers,
        json={"teacher_id": ada.id},
    )
    assert replaced.json()["class_teacher_id"] == ada.id
    session.expire_all()
    assert session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "class.teacher_assigned")
    ) == 2

    subject_assigned = client.put(
        f"/admin/subjects/{subject_id}/teacher",
        headers=headers,
        json={"teacher_id": grace.id},
    )
    assert subject_assigned.status_code == 200, subject_assigned.text
    assert subject_assigned.json()["teacher_id"] == grace.id
    client.put(
        f"/admin/subjects/{subject_id}/teacher",
        headers=headers,
        json={"teacher_id": grace.id},
    )
    cleared = client.delete(f"/admin/subjects/{subject_id}/teacher", headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["teacher_id"] is None
    cleared_again = client.delete(f"/admin/subjects/{subject_id}/teacher", headers=headers)
    assert cleared_again.status_code == 200
    assert cleared_again.json()["teacher_id"] is None

    removed = client.delete(f"/admin/classes/{class_id}/teacher", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["class_teacher_id"] is None
    session.expire_all()
    stored = session.get(SchoolClass, class_id)
    assert stored is not None
    assert stored.class_teacher_id is None


def test_enrollment_avoids_duplicate_rows_and_supports_move(roster_client) -> None:
    client, session = roster_client
    headers = _admin(client, session)
    student = _student(session, email="ada@school.edu", roll_number="R-1")
    inactive = _student(session, email="inactive@school.edu", roll_number="R-2", active=False)
    first = client.post("/admin/classes", headers=headers, json=_class_body())
    second = client.post(
        "/admin/classes",
        headers=headers,
        json=_class_body(name="Grade 11", section="A"),
    )
    closed = client.post(
        "/admin/classes",
        headers=headers,
        json=_class_body(name="Grade 9", section="B", status="INACTIVE"),
    )
    assert first.status_code == second.status_code == closed.status_code == 201
    class_a = first.json()["class_id"]
    class_b = second.json()["class_id"]

    enrolled = client.post(
        f"/admin/classes/{class_a}/students",
        headers=headers,
        json={"student_id": student.id},
    )
    assert enrolled.status_code == 201, enrolled.text
    assert enrolled.json()["student_id"] == student.id
    assert enrolled.json()["class_id"] == class_a
    assert enrolled.json()["status"] == "ACTIVE"
    assert enrolled.json()["roll_number"] == "R-1"

    duplicate = client.post(
        f"/admin/classes/{class_a}/students",
        headers=headers,
        json={"student_id": student.id},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Student is already enrolled in this class"
    elsewhere = client.post(
        f"/admin/classes/{class_b}/students",
        headers=headers,
        json={"student_id": student.id},
    )
    assert elsewhere.status_code == 409
    assert elsewhere.json()["detail"] == "Student is already enrolled in another class"
    inactive_student = client.post(
        f"/admin/classes/{class_b}/students",
        headers=headers,
        json={"student_id": inactive.id},
    )
    assert inactive_student.status_code == 409
    assert inactive_student.json()["detail"] == "Student is not active"
    inactive_class = client.post(
        f"/admin/classes/{closed.json()['class_id']}/students",
        headers=headers,
        json={"student_id": student.id},
    )
    assert inactive_class.status_code == 409
    assert inactive_class.json()["detail"] == "Class is not active"

    moved = client.post(
        f"/admin/classes/{class_b}/students/{student.id}/move",
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["class_id"] == class_b
    assert moved.json()["status"] == "ACTIVE"
    session.expire_all()
    rows = session.scalars(select(Enrollment).order_by(Enrollment.class_id)).all()
    assert len(rows) == 2
    by_class = {row.class_id: row.status for row in rows}
    assert by_class[class_a] == EnrollmentStatus.WITHDRAWN
    assert by_class[class_b] == EnrollmentStatus.ACTIVE

    back = client.post(f"/admin/classes/{class_a}/students/{student.id}/move", headers=headers)
    assert back.status_code == 200, back.text
    assert back.json()["class_id"] == class_a
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Enrollment)) == 2
    active = session.scalar(
        select(Enrollment).where(
            Enrollment.student_id == student.id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
    )
    assert active is not None
    assert active.class_id == class_a

    roster = client.get(f"/admin/classes/{class_a}/students", headers=headers)
    assert roster.status_code == 200
    assert [item["student_id"] for item in roster.json()["items"]] == [student.id]
    removed = client.delete(f"/admin/classes/{class_a}/students/{student.id}", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["status"] == "WITHDRAWN"
    assert client.get(f"/admin/classes/{class_a}/students", headers=headers).json()["items"] == []
    withdrawn = client.get(
        f"/admin/classes/{class_a}/students",
        headers=headers,
        params={"status": "WITHDRAWN"},
    )
    assert withdrawn.json()["total"] == 1
    missing = client.delete(f"/admin/classes/{class_a}/students/{student.id}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Student is not enrolled in this class"

    restored = client.post(
        f"/admin/classes/{class_a}/students",
        headers=headers,
        json={"student_id": student.id},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "ACTIVE"
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(Enrollment)) == 2


def test_teacher_sees_only_assigned_classes_and_subjects(roster_client) -> None:
    client, session = roster_client
    headers = _admin(client, session)
    grace = _teacher(session, email="grace@school.edu", employee_id="T-1")
    ada = _teacher(session, email="ada@school.edu", employee_id="T-2")
    grace_headers = _login(client, "grace@school.edu")
    ada_headers = _login(client, "ada@school.edu")
    created = client.post("/admin/classes", headers=headers, json=_class_body())
    assert created.status_code == 201, created.text
    class_id = created.json()["class_id"]
    math = client.post(
        "/admin/subjects",
        headers=headers,
        json={"class_id": class_id, "name": "Mathematics", "code": "MATH"},
    )
    physics = client.post(
        "/admin/subjects",
        headers=headers,
        json={"class_id": class_id, "name": "Physics", "code": "PHY"},
    )
    assert math.status_code == physics.status_code == 201
    math_id = math.json()["subject_id"]
    physics_id = physics.json()["subject_id"]

    assert client.get("/teacher/classes", headers=grace_headers).json()["total"] == 0
    assert client.get(f"/teacher/classes/{class_id}", headers=grace_headers).status_code == 404

    client.put(
        f"/admin/classes/{class_id}/teacher",
        headers=headers,
        json={"teacher_id": grace.id},
    )
    client.put(
        f"/admin/subjects/{math_id}/teacher",
        headers=headers,
        json={"teacher_id": grace.id},
    )
    client.put(
        f"/admin/subjects/{physics_id}/teacher",
        headers=headers,
        json={"teacher_id": ada.id},
    )

    grace_classes = client.get("/teacher/classes", headers=grace_headers)
    assert grace_classes.status_code == 200, grace_classes.text
    assert [item["class_id"] for item in grace_classes.json()["items"]] == [class_id]
    assert client.get(f"/teacher/classes/{class_id}", headers=grace_headers).status_code == 200
    grace_subjects = client.get("/teacher/subjects", headers=grace_headers)
    assert [item["code"] for item in grace_subjects.json()["items"]] == ["MATH"]
    assert client.get(f"/teacher/subjects/{math_id}", headers=grace_headers).status_code == 200
    assert client.get(f"/teacher/subjects/{physics_id}", headers=grace_headers).status_code == 404

    ada_subjects = client.get("/teacher/subjects", headers=ada_headers)
    assert [item["code"] for item in ada_subjects.json()["items"]] == ["PHY"]
    ada_classes = client.get("/teacher/classes", headers=ada_headers)
    assert [item["class_id"] for item in ada_classes.json()["items"]] == [class_id]

    client.delete(f"/admin/classes/{class_id}", headers=headers)
    assert client.get("/teacher/classes", headers=grace_headers).json()["total"] == 0
    assert client.get("/teacher/subjects", headers=ada_headers).json()["total"] == 0
    assert client.get(f"/teacher/classes/{class_id}", headers=grace_headers).status_code == 404


def test_academic_routes_require_the_matching_role(roster_client) -> None:
    client, session = roster_client
    admin_headers = _admin(client, session)
    _user(session, email="teacher@school.edu", role=UserRole.TEACHER)
    _user(session, email="student@school.edu", role=UserRole.STUDENT)
    teacher_headers = _login(client, "teacher@school.edu")
    student_headers = _login(client, "student@school.edu")
    created = client.post("/admin/classes", headers=admin_headers, json=_class_body())
    assert created.status_code == 201, created.text
    class_id = created.json()["class_id"]

    for headers in (teacher_headers, student_headers):
        assert client.get("/admin/classes", headers=headers).status_code == 403
        assert client.post("/admin/classes", headers=headers, json=_class_body()).status_code == 403
        assert client.get(f"/admin/classes/{class_id}", headers=headers).status_code == 403
        assert client.get("/admin/subjects", headers=headers).status_code == 403

    assert client.get("/admin/classes").status_code == 401
    assert client.get("/teacher/classes").status_code == 401
    assert client.get("/teacher/subjects", headers=student_headers).status_code == 403
    assert client.get("/teacher/classes", headers=admin_headers).status_code == 403
    missing_profile = client.get("/teacher/classes", headers=teacher_headers)
    assert missing_profile.status_code == 404
    assert missing_profile.json()["detail"] == "Teacher profile not found"
