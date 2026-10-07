"""Teacher dashboard reads, sessions, and report export.

A teacher sees assigned classes, their subjects, and the students in those
classes. Another teacher's class is hidden. Admins and students cannot call
the teacher routes. These tests do not need MySQL, dlib, or a camera.
"""

from datetime import date

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.recognition import RecognitionResult
from app.auth.passwords import hash_password
from app.config import get_settings
from app.database.connection import get_db
from app.main import create_app
from app.models import (
    AttendanceSession,
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
from app.routers.attendance import get_recognizer

TEST_JWT_SECRET = "test-jwt-secret-must-be-32-bytes-or-more"
PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def jwt_test_settings(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    monkeypatch.setenv("ATTENDANCE_TIMEZONE", "UTC")
    monkeypatch.setenv("ATTENDANCE_LATE_AFTER", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def roster():
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

    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    def override_get_db():
        yield session

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, session
    session.close()
    engine.dispose()


def _user(session, *, email: str, role: UserRole, first_name: str) -> User:
    user = User(
        first_name=first_name,
        last_name="User",
        email=email,
        password_hash=hash_password(PASSWORD),
        role=role,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _login(client, email: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _teacher(session, *, email: str, code: str, first_name: str) -> Teacher:
    user = _user(session, email=email, role=UserRole.TEACHER, first_name=first_name)
    teacher = Teacher(user_id=user.id, employee_code=code)
    session.add(teacher)
    session.commit()
    session.refresh(teacher)
    return teacher


def _student(session, *, email: str, roll: str, first_name: str) -> Student:
    user = _user(session, email=email, role=UserRole.STUDENT, first_name=first_name)
    student = Student(user_id=user.id, roll_number=roll)
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


def _class(session, teacher: Teacher | None, *, section: str, active: bool = True) -> SchoolClass:
    school_class = SchoolClass(
        name="Grade 10",
        section=section,
        academic_year="2026-2027",
        class_teacher_id=None if teacher is None else teacher.id,
        is_active=active,
    )
    session.add(school_class)
    session.commit()
    session.refresh(school_class)
    return school_class


def _subject(session, school_class: SchoolClass, teacher: Teacher | None, *, code: str) -> Subject:
    subject = Subject(
        class_id=school_class.id,
        teacher_id=None if teacher is None else teacher.id,
        name="Mathematics" if code == "MATH" else code,
        code=code,
        is_active=True,
    )
    session.add(subject)
    session.commit()
    session.refresh(subject)
    return subject


def _enroll(session, student: Student, school_class: SchoolClass) -> None:
    session.add(
        Enrollment(
            student_id=student.id,
            class_id=school_class.id,
            status=EnrollmentStatus.ACTIVE,
            enrolled_on=date(2026, 8, 1),
        )
    )
    session.commit()


def _png() -> bytes:
    image = np.full((32, 32, 3), 128, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _use(client, student_id: int) -> None:
    def recognize(_image: bytes, *, db) -> RecognitionResult:
        return RecognitionResult(
            student_id=student_id,
            matched=True,
            confidence=0.75,
            distance=0.25,
            faces=(),
        )

    client.app.dependency_overrides[get_recognizer] = lambda: recognize


def _mark(client, headers, school_class: SchoolClass, subject: Subject):
    return client.post(
        "/attendance/mark",
        headers=headers,
        data={"class_id": str(school_class.id), "subject_id": str(subject.id)},
        files={"image": ("frame.png", _png(), "image/png")},
    )


def test_teacher_dashboard_is_limited_to_assigned_work(roster) -> None:
    client, session = roster
    admin = _login(client, _user(session, email="admin@school.edu", role=UserRole.ADMIN, first_name="Ada").email)
    grace = _teacher(session, email="grace@school.edu", code="T-014", first_name="Grace")
    ada = _teacher(session, email="ada@school.edu", code="T-015", first_name="Ada")
    outsider = _teacher(session, email="out@school.edu", code="T-099", first_name="Out")
    grace_headers = _login(client, grace.user.email)
    ada_headers = _login(client, ada.user.email)
    outsider_headers = _login(client, outsider.user.email)
    homeroom_student = _student(session, email="ada.student@school.edu", roll="2026-014", first_name="Alan")
    other_student = _student(session, email="other.student@school.edu", roll="2026-099", first_name="Other")
    student_headers = _login(client, homeroom_student.user.email)
    school_class = _class(session, grace, section="A")
    other_class = _class(session, outsider, section="B")
    math = _subject(session, school_class, ada, code="MATH")
    science = _subject(session, school_class, grace, code="SCI")
    history = _subject(session, other_class, outsider, code="HIST")
    _enroll(session, homeroom_student, school_class)
    _enroll(session, other_student, other_class)
    _use(client, homeroom_student.id)
    assert _mark(client, admin, school_class, math).status_code == 201
    _use(client, homeroom_student.id)
    assert _mark(client, admin, school_class, science).status_code == 201
    _use(client, other_student.id)
    assert _mark(client, admin, other_class, history).status_code == 201

    students = client.get(f"/teacher/classes/{school_class.id}/students", headers=ada_headers)
    assert students.status_code == 200, students.text
    assert students.json()["total"] == 1
    assert students.json()["items"][0]["roll_number"] == "2026-014"
    assert "email" not in students.json()["items"][0]
    assert client.get(f"/teacher/classes/{other_class.id}/students", headers=ada_headers).status_code == 404
    assert client.get(f"/teacher/classes/{school_class.id}/students", headers=outsider_headers).status_code == 404
    assert client.get(f"/teacher/classes/{school_class.id}/students", headers=admin).status_code == 403
    assert client.get(f"/teacher/classes/{school_class.id}/students", headers=student_headers).status_code == 403
    assert client.get("/admin/students", headers=ada_headers).status_code == 403
    assert client.get("/admin/classes", headers=ada_headers).status_code == 403
    assert client.get(f"/teacher/classes/{school_class.id}/students?page=0", headers=ada_headers).status_code == 422

    ada_today = client.get("/teacher/attendance/today", headers=ada_headers)
    assert ada_today.status_code == 200, ada_today.text
    assert [item["subject_id"] for item in ada_today.json()["items"]] == [math.id]
    grace_today = client.get("/teacher/attendance/today", headers=grace_headers)
    assert {item["subject_id"] for item in grace_today.json()["items"]} == {math.id, science.id}
    hidden_class = client.get(f"/classes/{other_class.id}/attendance", headers=ada_headers)
    assert hidden_class.status_code == 404
    class_roll = client.get(f"/classes/{school_class.id}/attendance", headers=ada_headers)
    assert [item["subject_id"] for item in class_roll.json()["items"]] == [math.id]
    history = client.get(f"/students/{homeroom_student.id}/attendance", headers=ada_headers)
    assert history.json()["total"] == 1
    assert client.get(f"/students/{other_student.id}/attendance", headers=ada_headers).status_code == 404

    stats = client.get("/teacher/attendance/statistics", headers=ada_headers)
    assert stats.status_code == 200, stats.text
    assert stats.json() == {"present": 1, "absent": 0, "late": 0, "excused": 0, "total": 1}
    grace_stats = client.get("/teacher/attendance/statistics", headers=grace_headers)
    assert grace_stats.json()["present"] == 2
    ranged = client.get(
        "/teacher/attendance?date_from=2026-10-07&date_to=2026-10-01",
        headers=ada_headers,
    )
    assert ranged.status_code == 422
    listed = client.get("/teacher/attendance", headers=ada_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    exported = client.get("/teacher/attendance/export", headers=ada_headers)
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/csv")
    assert "MATH" in exported.text
    assert "SCI" not in exported.text
    assert "2026-099" not in exported.text
    grace_export = client.get("/teacher/attendance/export", headers=grace_headers)
    assert "MATH" in grace_export.text
    assert "SCI" in grace_export.text
    assert "2026-099" not in grace_export.text

    denied_subject = client.post(
        "/teacher/attendance/sessions",
        headers=ada_headers,
        json={"class_id": school_class.id, "subject_id": science.id},
    )
    assert denied_subject.status_code == 404
    opened = client.post(
        "/teacher/attendance/sessions",
        headers=ada_headers,
        json={"class_id": school_class.id, "subject_id": math.id},
    )
    assert opened.status_code == 201, opened.text
    assert opened.json()["created"] is True
    assert opened.json()["status"] == "OPEN"
    again = client.post(
        "/teacher/attendance/sessions",
        headers=ada_headers,
        json={"class_id": school_class.id, "subject_id": math.id},
    )
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False
    assert again.json()["session_id"] == opened.json()["session_id"]
    assert session.scalar(select(func.count(AttendanceSession.id))) == 1
    closed = client.post(
        f"/teacher/attendance/sessions/{opened.json()['session_id']}/close",
        headers=ada_headers,
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    stolen = client.post(
        f"/teacher/attendance/sessions/{opened.json()['session_id']}/close",
        headers=grace_headers,
    )
    assert stolen.status_code == 404
    assert client.post(
        "/teacher/attendance/sessions",
        headers=admin,
        json={"class_id": school_class.id, "subject_id": math.id},
    ).status_code == 403
    assert client.post(
        "/teacher/attendance/sessions",
        headers=student_headers,
        json={"class_id": school_class.id, "subject_id": math.id},
    ).status_code == 403
    assert session.scalar(
        select(func.count(AuditLog.id)).where(AuditLog.action == "attendance.session_started")
    ) == 1


def test_a_session_requires_an_open_assigned_lesson(roster) -> None:
    client, session = roster
    teacher = _teacher(session, email="grace@school.edu", code="T-014", first_name="Grace")
    headers = _login(client, teacher.user.email)
    closed = _class(session, teacher, section="C", active=False)
    subject = _subject(session, closed, teacher, code="MATH")
    _user(session, email="lin@school.edu", role=UserRole.TEACHER, first_name="Lin")
    lin = _login(client, "lin@school.edu")

    inactive = client.post(
        "/teacher/attendance/sessions",
        headers=headers,
        json={"class_id": closed.id, "subject_id": subject.id},
    )
    assert inactive.status_code == 409
    assert inactive.json()["detail"] == "Class is not active"
    assert client.get("/teacher/attendance/today", headers=lin).status_code == 404
    assert client.get("/teacher/attendance/today").status_code == 401
