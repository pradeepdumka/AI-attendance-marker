"""Student dashboard reads and authorization.

A student sees their own profile, class, subjects, history, percentage,
month, and calendar. Another student's marks stay hidden. Admins and
teachers cannot call the student routes, and a student cannot mark
attendance or open admin and teacher management. These tests do not
need MySQL, dlib, or a camera.
"""

from datetime import date, datetime, timezone

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
    Attendance,
    AttendanceMethod,
    AttendanceStatus,
    Base,
    Enrollment,
    EnrollmentStatus,
    Gender,
    SchoolClass,
    Student,
    Subject,
    Teacher,
    User,
    UserRole,
)

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


def _class(session, teacher: Teacher | None, *, section: str) -> SchoolClass:
    school_class = SchoolClass(
        name="Grade 10",
        section=section,
        academic_year="2026-2027",
        class_teacher_id=None if teacher is None else teacher.id,
        is_active=True,
    )
    session.add(school_class)
    session.commit()
    session.refresh(school_class)
    return school_class


def _subject(
    session,
    school_class: SchoolClass,
    teacher: Teacher | None,
    *,
    code: str,
    name: str,
    active: bool = True,
) -> Subject:
    subject = Subject(
        class_id=school_class.id,
        teacher_id=None if teacher is None else teacher.id,
        name=name,
        code=code,
        is_active=active,
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


def _mark(
    session,
    *,
    student: Student,
    school_class: SchoolClass,
    subject: Subject,
    day: date,
    status: AttendanceStatus,
) -> None:
    session.add(
        Attendance(
            student_id=student.id,
            class_id=school_class.id,
            subject_id=subject.id,
            attendance_date=day,
            status=status,
            method=AttendanceMethod.MANUAL,
            marked_at=datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc),
        )
    )
    session.commit()


def test_student_dashboard_shows_only_own_records(roster) -> None:
    client, session = roster
    admin = _login(client, _user(session, email="admin@school.edu", role=UserRole.ADMIN, first_name="Ada").email)
    teacher = _teacher(session, email="grace@school.edu", code="T-014", first_name="Grace")
    teacher_headers = _login(client, teacher.user.email)
    ada = _student(session, email="ada@school.edu", roll="2026-014", first_name="Ada")
    ada.date_of_birth = date(2010, 12, 10)
    ada.gender = Gender.FEMALE
    ada.user.phone = "555-0100"
    session.commit()
    alan = _student(session, email="alan@school.edu", roll="2026-099", first_name="Alan")
    ada_headers = _login(client, ada.user.email)
    alan_headers = _login(client, alan.user.email)
    school_class = _class(session, teacher, section="A")
    other_class = _class(session, teacher, section="B")
    math = _subject(session, school_class, teacher, code="MATH", name="Mathematics")
    science = _subject(session, school_class, teacher, code="SCI", name="Science")
    art = _subject(session, school_class, teacher, code="ART", name="Art")
    excused = _subject(session, school_class, None, code="EXC", name="Excused lab")
    music = _subject(session, school_class, teacher, code="MUSIC", name="Music", active=False)
    history = _subject(session, other_class, teacher, code="HIST", name="History")
    _enroll(session, ada, school_class)
    _enroll(session, alan, other_class)
    _mark(session, student=ada, school_class=school_class, subject=math, day=date(2026, 10, 1), status=AttendanceStatus.PRESENT)
    _mark(session, student=ada, school_class=school_class, subject=math, day=date(2026, 10, 2), status=AttendanceStatus.ABSENT)
    _mark(session, student=ada, school_class=school_class, subject=math, day=date(2026, 9, 30), status=AttendanceStatus.ABSENT)
    _mark(session, student=ada, school_class=school_class, subject=science, day=date(2026, 10, 1), status=AttendanceStatus.LATE)
    _mark(session, student=ada, school_class=school_class, subject=science, day=date(2026, 10, 3), status=AttendanceStatus.EXCUSED)
    _mark(session, student=ada, school_class=school_class, subject=excused, day=date(2026, 10, 4), status=AttendanceStatus.EXCUSED)
    _mark(session, student=ada, school_class=school_class, subject=music, day=date(2026, 9, 15), status=AttendanceStatus.PRESENT)
    _mark(session, student=alan, school_class=other_class, subject=history, day=date(2026, 10, 1), status=AttendanceStatus.PRESENT)
    before = session.scalar(select(func.count(Attendance.id)))

    profile = client.get("/student/profile", headers=ada_headers)
    assert profile.status_code == 200, profile.text
    assert profile.json()["email"] == "ada@school.edu"
    assert profile.json()["roll_number"] == "2026-014"
    assert profile.json()["phone"] == "555-0100"
    assert profile.json()["gender"] == "FEMALE"
    assert profile.json()["class_id"] == school_class.id
    assert "password" not in profile.json()
    assert "alan@school.edu" not in profile.text

    school = client.get("/student/class", headers=ada_headers)
    assert school.status_code == 200, school.text
    assert school.json()["name"] == "Grade 10"
    assert school.json()["section"] == "A"
    assert school.json()["academic_year"] == "2026-2027"
    assert client.get("/student/class", headers=alan_headers).json()["section"] == "B"

    subjects = client.get("/student/subjects", headers=ada_headers)
    assert subjects.status_code == 200, subjects.text
    assert [item["code"] for item in subjects.json()["items"]] == ["ART", "EXC", "MATH", "SCI"]
    assert subjects.json()["total"] == 4
    narrowed = client.get("/student/subjects?search=math", headers=ada_headers)
    assert [item["code"] for item in narrowed.json()["items"]] == ["MATH"]

    history_rows = client.get("/student/attendance", headers=ada_headers)
    assert history_rows.status_code == 200, history_rows.text
    assert history_rows.json()["total"] == 7
    assert {item["student_id"] for item in history_rows.json()["items"]} == {ada.id}
    assert "2026-099" not in history_rows.text
    math_only = client.get(f"/student/attendance?subject_id={math.id}", headers=ada_headers)
    assert math_only.json()["total"] == 3
    ranged = client.get(
        "/student/attendance?date_from=2026-10-07&date_to=2026-10-01",
        headers=ada_headers,
    )
    assert ranged.status_code == 422

    percentage = client.get("/student/attendance/percentage", headers=ada_headers)
    assert percentage.status_code == 200, percentage.text
    assert percentage.json() == {
        "present": 2,
        "absent": 2,
        "late": 1,
        "excused": 2,
        "total": 7,
        "percentage": 60.0,
    }
    math_rate = client.get(f"/student/attendance/percentage?subject_id={math.id}", headers=ada_headers)
    assert math_rate.json()["percentage"] == 33.3
    hidden_subject = client.get(
        f"/student/attendance/percentage?subject_id={history.id}",
        headers=ada_headers,
    )
    assert hidden_subject.json()["total"] == 0
    assert hidden_subject.json()["percentage"] is None
    alan_rate = client.get("/student/attendance/percentage", headers=alan_headers)
    assert alan_rate.json() == {
        "present": 1,
        "absent": 0,
        "late": 0,
        "excused": 0,
        "total": 1,
        "percentage": 100.0,
    }

    by_subject = client.get("/student/attendance/by-subject", headers=ada_headers)
    assert by_subject.status_code == 200, by_subject.text
    rates = {item["code"]: item for item in by_subject.json()["items"]}
    assert list(rates) == ["ART", "EXC", "MATH", "MUSIC", "SCI"]
    assert "HIST" not in rates
    assert rates["MATH"]["percentage"] == 33.3
    assert rates["SCI"]["percentage"] == 100.0
    assert rates["EXC"]["percentage"] is None
    assert rates["ART"] == {
        "subject_id": art.id,
        "class_id": school_class.id,
        "name": "Art",
        "code": "ART",
        "present": 0,
        "absent": 0,
        "late": 0,
        "excused": 0,
        "total": 0,
        "percentage": None,
    }
    assert rates["MUSIC"]["present"] == 1

    month = client.get("/student/attendance/monthly?year=2026&month=10", headers=ada_headers)
    assert month.status_code == 200, month.text
    assert month.json()["year"] == 2026
    assert month.json()["month"] == 10
    assert month.json()["percentage"] == 66.7
    september = client.get("/student/attendance/monthly?year=2026&month=9", headers=ada_headers)
    assert september.json()["percentage"] == 50.0
    assert client.get("/student/attendance/monthly?year=2026", headers=ada_headers).status_code == 422
    assert client.get("/student/attendance/monthly?month=13", headers=ada_headers).status_code == 422

    calendar = client.get("/student/attendance/calendar?year=2026&month=10", headers=ada_headers)
    assert calendar.status_code == 200, calendar.text
    assert calendar.json()["percentage"] == 66.7
    assert [(day["date"], day["status"]) for day in calendar.json()["days"]] == [
        ("2026-10-01", "MIXED"),
        ("2026-10-02", "ABSENT"),
        ("2026-10-03", "EXCUSED"),
        ("2026-10-04", "EXCUSED"),
    ]
    math_month = client.get(
        f"/student/attendance/calendar?year=2026&month=10&subject_id={math.id}",
        headers=ada_headers,
    )
    assert [(day["date"], day["status"]) for day in math_month.json()["days"]] == [
        ("2026-10-01", "PRESENT"),
        ("2026-10-02", "ABSENT"),
    ]
    assert math_month.json()["percentage"] == 50.0
    alan_day = client.get("/student/attendance/calendar?year=2026&month=10", headers=alan_headers)
    assert [(day["date"], day["status"]) for day in alan_day.json()["days"]] == [("2026-10-01", "PRESENT")]
    assert "2026-014" not in alan_day.text

    for path in (
        "/admin/me",
        "/admin/students",
        "/admin/teachers",
        "/admin/classes",
        "/teacher/me",
        "/teacher/profile",
        "/teacher/classes",
        "/teacher/subjects",
        "/teacher/attendance",
        "/teacher/attendance/today",
        "/teacher/attendance/statistics",
        "/teacher/attendance/export",
        "/attendance/today",
        f"/students/{alan.id}/attendance",
        f"/students/{ada.id}/attendance",
        f"/students/{alan.id}/face",
        f"/classes/{school_class.id}/attendance",
    ):
        assert client.get(path, headers=ada_headers).status_code == 403, path

    manual = client.post(
        "/attendance/manual",
        headers=ada_headers,
        json={
            "student_id": ada.id,
            "class_id": school_class.id,
            "subject_id": math.id,
            "status": "PRESENT",
        },
    )
    assert manual.status_code == 403
    marked = client.post(
        "/attendance/mark",
        headers=ada_headers,
        data={"class_id": str(school_class.id), "subject_id": str(math.id)},
        files={"image": ("frame.png", b"not-a-camera-frame", "image/png")},
    )
    assert marked.status_code == 403
    assert client.post(
        "/teacher/attendance/sessions",
        headers=ada_headers,
        json={"class_id": school_class.id, "subject_id": math.id},
    ).status_code == 403
    assert session.scalar(select(func.count(Attendance.id))) == before

    for headers in (admin, teacher_headers):
        assert client.get("/student/class", headers=headers).status_code == 403
        assert client.get("/student/subjects", headers=headers).status_code == 403
        assert client.get("/student/attendance/percentage", headers=headers).status_code == 403
        assert client.get("/student/attendance/by-subject", headers=headers).status_code == 403
        assert client.get("/student/attendance/monthly", headers=headers).status_code == 403
        assert client.get("/student/attendance/calendar", headers=headers).status_code == 403
    assert client.get("/student/profile").status_code == 401
    assert client.get("/student/attendance/percentage").status_code == 401


def test_a_student_without_a_class_or_profile_is_limited(roster) -> None:
    client, session = roster
    enrolled = _student(session, email="lin@school.edu", roll="2026-020", first_name="Lin")
    _user(session, email="no-profile@school.edu", role=UserRole.STUDENT, first_name="No")
    enrolled_headers = _login(client, enrolled.user.email)
    missing_headers = _login(client, "no-profile@school.edu")

    empty_class = client.get("/student/class", headers=enrolled_headers)
    assert empty_class.status_code == 404
    assert empty_class.json()["detail"] == "You are not enrolled in a class"
    subjects = client.get("/student/subjects", headers=enrolled_headers)
    assert subjects.status_code == 200, subjects.text
    assert subjects.json()["total"] == 0
    percentage = client.get("/student/attendance/percentage", headers=enrolled_headers)
    assert percentage.json()["total"] == 0
    assert percentage.json()["percentage"] is None

    for path in (
        "/student/profile",
        "/student/class",
        "/student/subjects",
        "/student/attendance",
        "/student/attendance/percentage",
        "/student/attendance/by-subject",
        "/student/attendance/monthly",
        "/student/attendance/calendar",
    ):
        missing = client.get(path, headers=missing_headers)
        assert missing.status_code == 404, path
        assert missing.json()["detail"] == "Student profile not found"
