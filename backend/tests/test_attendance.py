"""Attendance marking and reads. These tests do not need MySQL, dlib, or a camera."""

from datetime import date, datetime, timedelta, timezone

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.recognition import RecognitionResult
from app.auth.passwords import hash_password
from app.config import get_settings
from app.database.connection import get_db
from app.main import create_app
from app.models import (
    Attendance,
    AttendanceMethod,
    AttendanceStatus,
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
from app.services import attendance as attendance_service
from app.services.attendance import (
    StudentNotInClass,
    StudentNotRecognized,
    TeacherNotIdentified,
    mark_from_frame,
    mark_manual,
)

TEST_JWT_SECRET = "test-jwt-secret-must-be-32-bytes-or-more"
PASSWORD = "correct-horse-battery"
UTC = timezone.utc
_MARK_FIELDS = {
    "attendance_id",
    "student_id",
    "roll_number",
    "first_name",
    "last_name",
    "class_id",
    "subject_id",
    "teacher_id",
    "date",
    "check_in_time",
    "status",
    "confidence_score",
    "recognition_method",
    "created",
}


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
    """SQLite session and API client. Attendance routes do not open MySQL."""
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


def _user(session, *, email: str, role: UserRole, active: bool = True, first_name: str = "Pat") -> User:
    user = User(
        first_name=first_name,
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


def _login(client, email: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _headers(client, session, *, email: str, role: UserRole, first_name: str = "Pat") -> dict[str, str]:
    _user(session, email=email, role=role, first_name=first_name)
    return _login(client, email)


def _student(session, *, email: str, roll: str, active: bool = True, first_name: str = "Ada") -> Student:
    user = _user(session, email=email, role=UserRole.STUDENT, active=active, first_name=first_name)
    student = Student(user_id=user.id, roll_number=roll)
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


def _teacher(session, *, email: str, code: str, first_name: str = "Grace") -> Teacher:
    user = _user(session, email=email, role=UserRole.TEACHER, first_name=first_name)
    teacher = Teacher(user_id=user.id, employee_code=code)
    session.add(teacher)
    session.commit()
    session.refresh(teacher)
    return teacher


def _class(session, teacher: Teacher | None, *, section: str = "A", active: bool = True) -> SchoolClass:
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


def _subject(
    session,
    school_class: SchoolClass,
    teacher: Teacher | None,
    *,
    code: str = "MATH",
    active: bool = True,
) -> Subject:
    subject = Subject(
        class_id=school_class.id,
        teacher_id=None if teacher is None else teacher.id,
        name="Mathematics" if code == "MATH" else code,
        code=code,
        is_active=active,
    )
    session.add(subject)
    session.commit()
    session.refresh(subject)
    return subject


def _enroll(session, student: Student, school_class: SchoolClass, *, status: EnrollmentStatus = EnrollmentStatus.ACTIVE) -> None:
    session.add(
        Enrollment(
            student_id=student.id,
            class_id=school_class.id,
            status=status,
            enrolled_on=date(2026, 8, 1),
        )
    )
    session.commit()


def _png() -> bytes:
    image = np.full((32, 32, 3), 128, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _files() -> dict:
    return {"image": ("frame.png", _png(), "image/png")}


def _recognize(student_id: int | None, *, confidence: float = 0.75, matched: bool = True):
    def recognize(_image: bytes, *, db) -> RecognitionResult:
        return RecognitionResult(
            student_id=student_id,
            matched=matched and student_id is not None,
            confidence=confidence if matched else 0.0,
            distance=0.25 if matched else None,
            faces=(),
        )

    return recognize


def _use(client, student_id: int | None, *, matched: bool = True, confidence: float = 0.75) -> None:
    client.app.dependency_overrides[get_recognizer] = lambda: _recognize(
        student_id,
        matched=matched,
        confidence=confidence,
    )


def _mark(client, headers, school_class: SchoolClass, subject: Subject):
    return client.post(
        "/attendance/mark",
        headers=headers,
        data={"class_id": str(school_class.id), "subject_id": str(subject.id)},
        files=_files(),
    )


def _count(session) -> int:
    return int(session.scalar(select(func.count()).select_from(Attendance)) or 0)


def test_a_frame_marks_the_recognized_student_once(roster) -> None:
    client, session = roster
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN, first_name="Admin")
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    student = _student(session, email="ada@school.edu", roll="2026-014")
    school_class = _class(session, teacher)
    subject = _subject(session, school_class, teacher)
    _enroll(session, student, school_class)
    _use(client, student.id, confidence=0.75)

    created = _mark(client, headers, school_class, subject)
    duplicate = _mark(client, headers, school_class, subject)

    assert created.status_code == 201, created.text
    assert created.headers["location"] == (
        f"/classes/{school_class.id}/attendance?subject_id={subject.id}"
    )
    body = created.json()
    assert set(body) == _MARK_FIELDS
    assert body["created"] is True
    assert body["student_id"] == student.id
    assert body["class_id"] == school_class.id
    assert body["subject_id"] == subject.id
    assert body["teacher_id"] == teacher.id
    assert body["status"] == "PRESENT"
    assert body["confidence_score"] == pytest.approx(0.75)
    assert body["recognition_method"] == "FACE_RECOGNITION"
    assert body["roll_number"] == "2026-014"
    assert body["first_name"] == "Ada"
    assert _png() not in created.content

    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["created"] is False
    assert duplicate.json()["attendance_id"] == body["attendance_id"]
    assert _count(session) == 1

    session.expire_all()
    row = session.get(Attendance, body["attendance_id"])
    assert row is not None
    assert row.student_id == student.id
    assert row.class_id == school_class.id
    assert row.subject_id == subject.id
    assert row.teacher_id == teacher.id
    assert row.status == AttendanceStatus.PRESENT
    assert row.method == AttendanceMethod.FACE_RECOGNITION
    assert float(row.confidence) == pytest.approx(0.75)
    assert row.marked_at is not None
    audits = session.scalars(select(AuditLog)).all()
    assert [audit.action for audit in audits] == ["attendance.marked"]
    assert audits[0].details["student_id"] == student.id
    assert "image" not in audits[0].details


def test_an_unmatched_or_ineligible_student_is_not_stored(roster) -> None:
    client, session = roster
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    student = _student(session, email="ada@school.edu", roll="2026-014", active=False)
    other = _student(session, email="alan@school.edu", roll="2026-015", first_name="Alan")
    school_class = _class(session, teacher)
    other_class = _class(session, teacher, section="B")
    subject = _subject(session, school_class, teacher)
    _enroll(session, student, school_class)
    _enroll(session, other, other_class)

    _use(client, None, matched=False)
    missing = _mark(client, headers, school_class, subject)
    assert missing.status_code == 422
    assert missing.json()["detail"] == "No matching student"

    _use(client, student.id)
    inactive = _mark(client, headers, school_class, subject)
    assert inactive.status_code == 409
    assert inactive.json()["detail"] == "Student is not active"

    _use(client, other.id)
    outside = _mark(client, headers, school_class, subject)
    assert outside.status_code == 409
    assert outside.json()["detail"] == "Student is not enrolled in this class"
    assert _count(session) == 0


def test_staff_must_be_assigned_and_the_lesson_must_be_open(roster) -> None:
    client, session = roster
    admin = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    outsider = _teacher(session, email="outsider@school.edu", code="T-099", first_name="Out")
    outsider_headers = _login(client, outsider.user.email)
    student = _student(session, email="ada@school.edu", roll="2026-014")
    student_headers = _login(client, student.user.email)
    school_class = _class(session, teacher)
    subject = _subject(session, school_class, teacher)
    _enroll(session, student, school_class)
    _use(client, student.id)

    denied = _mark(client, student_headers, school_class, subject)
    assert denied.status_code == 403
    assert client.post("/attendance/mark", data={"class_id": "1", "subject_id": "1"}, files=_files()).status_code == 401

    rejected = _mark(client, outsider_headers, school_class, subject)
    assert rejected.status_code == 404
    assert rejected.json()["detail"] == "Subject not found"

    closed = _class(session, teacher, section="C", active=False)
    closed_subject = _subject(session, closed, teacher, code="SCI")
    _enroll(session, student, closed)
    inactive_class = _mark(client, admin, closed, closed_subject)
    assert inactive_class.status_code == 409
    assert inactive_class.json()["detail"] == "Class is not active"

    paused = _subject(session, school_class, teacher, code="ENG", active=False)
    inactive_subject = _mark(client, admin, school_class, paused)
    assert inactive_subject.status_code == 409
    assert inactive_subject.json()["detail"] == "Subject is not active"
    assert _count(session) == 0


def test_manual_statuses_do_not_replace_a_mark_or_each_other(roster) -> None:
    client, session = roster
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    student = _student(session, email="ada@school.edu", roll="2026-014")
    school_class = _class(session, teacher)
    subject = _subject(session, school_class, teacher)
    _enroll(session, student, school_class)
    payload = {
        "student_id": student.id,
        "class_id": school_class.id,
        "subject_id": subject.id,
        "status": "ABSENT",
    }

    created = client.post("/attendance/manual", headers=headers, json=payload)
    again = client.post("/attendance/manual", headers=headers, json={**payload, "status": "EXCUSED"})
    _use(client, student.id)
    faced = _mark(client, headers, school_class, subject)

    assert created.status_code == 201, created.text
    assert created.json()["status"] == "ABSENT"
    assert created.json()["recognition_method"] == "MANUAL"
    assert created.json()["confidence_score"] is None
    assert created.json()["created"] is True
    assert again.status_code == 200, again.text
    assert again.json()["created"] is False
    assert again.json()["status"] == "ABSENT"
    assert faced.status_code == 200
    assert faced.json()["status"] == "ABSENT"
    assert faced.json()["recognition_method"] == "MANUAL"
    assert _count(session) == 1


def test_today_class_and_student_history(roster) -> None:
    client, session = roster
    admin = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    other_teacher = _teacher(session, email="other@school.edu", code="T-015", first_name="Other")
    teacher_headers = _login(client, teacher.user.email)
    stranger = _login(client, other_teacher.user.email)
    ada = _student(session, email="ada@school.edu", roll="2026-014", first_name="Ada")
    alan = _student(session, email="alan@school.edu", roll="2026-015", first_name="Alan")
    student_headers = _login(client, ada.user.email)
    school_class = _class(session, teacher)
    other_class = _class(session, other_teacher, section="B")
    math = _subject(session, school_class, teacher, code="MATH")
    science = _subject(session, other_class, other_teacher, code="SCI")
    _enroll(session, ada, school_class)
    _enroll(session, alan, other_class)

    _use(client, ada.id)
    assert _mark(client, admin, school_class, math).status_code == 201
    _use(client, alan.id)
    assert _mark(client, admin, other_class, science).status_code == 201

    today = client.get("/attendance/today", headers=admin)
    assert today.status_code == 200, today.text
    assert today.json()["total"] == 2
    assert {item["student_id"] for item in today.json()["items"]} == {ada.id, alan.id}

    teacher_today = client.get("/attendance/today", headers=teacher_headers)
    assert teacher_today.status_code == 200, teacher_today.text
    assert [item["student_id"] for item in teacher_today.json()["items"]] == [ada.id]

    class_roll = client.get(f"/classes/{school_class.id}/attendance", headers=teacher_headers)
    assert class_roll.status_code == 200, class_roll.text
    assert [item["roll_number"] for item in class_roll.json()["items"]] == ["2026-014"]

    hidden = client.get(f"/classes/{school_class.id}/attendance", headers=stranger)
    assert hidden.status_code == 404
    assert hidden.json()["detail"] == "Class not found"

    history = client.get(f"/students/{ada.id}/attendance", headers=teacher_headers)
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["recognition_method"] == "FACE_RECOGNITION"

    own = client.get("/student/attendance", headers=student_headers)
    assert own.status_code == 200, own.text
    assert [item["student_id"] for item in own.json()["items"]] == [ada.id]
    blocked = client.get(f"/students/{alan.id}/attendance", headers=student_headers)
    assert blocked.status_code == 403

    ranged = client.get(
        f"/students/{ada.id}/attendance?date_from=2026-10-07&date_to=2026-10-01",
        headers=admin,
    )
    assert ranged.status_code == 422
    assert ranged.json()["detail"] == "date_from must be on or before date_to"


def test_a_bad_image_is_rejected_before_a_row_is_stored(roster) -> None:
    client, session = roster
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    school_class = _class(session, teacher)
    subject = _subject(session, school_class, teacher)

    response = client.post(
        "/attendance/mark",
        headers=headers,
        data={"class_id": str(school_class.id), "subject_id": str(subject.id)},
        files={"image": ("frame.png", b"not-an-image", "image/png")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Image could not be read"
    assert _count(session) == 0


def test_late_check_in_duplicate_day_and_database_constraint(roster, monkeypatch) -> None:
    _client, session = roster
    monkeypatch.setenv("ATTENDANCE_TIMEZONE", "UTC")
    monkeypatch.setenv("ATTENDANCE_LATE_AFTER", "09:15")
    get_settings.cache_clear()
    admin = _user(session, email="admin@school.edu", role=UserRole.ADMIN)
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    ada = _student(session, email="ada@school.edu", roll="2026-014")
    alan = _student(session, email="alan@school.edu", roll="2026-015", first_name="Alan")
    school_class = _class(session, teacher)
    math = _subject(session, school_class, teacher, code="MATH")
    science = _subject(session, school_class, None, code="SCI")
    _enroll(session, ada, school_class)
    _enroll(session, alan, school_class)

    early = mark_from_frame(
        session,
        actor=admin,
        class_id=school_class.id,
        subject_id=math.id,
        image_bytes=_png(),
        recognize=_recognize(ada.id, confidence=0.8),
        now=datetime(2026, 10, 6, 9, 14, tzinfo=UTC),
    )
    late = mark_from_frame(
        session,
        actor=admin,
        class_id=school_class.id,
        subject_id=science.id,
        image_bytes=_png(),
        recognize=_recognize(alan.id),
        now=datetime(2026, 10, 6, 9, 15, tzinfo=UTC),
    )
    repeated = mark_from_frame(
        session,
        actor=admin,
        class_id=school_class.id,
        subject_id=math.id,
        image_bytes=_png(),
        recognize=_recognize(ada.id),
        now=datetime(2026, 10, 6, 18, 0, tzinfo=UTC),
    )
    next_day = mark_from_frame(
        session,
        actor=admin,
        class_id=school_class.id,
        subject_id=math.id,
        image_bytes=_png(),
        recognize=_recognize(ada.id),
        now=datetime(2026, 10, 7, 8, 0, tzinfo=UTC),
    )

    assert early.created is True
    assert early.record.status == AttendanceStatus.PRESENT
    assert early.record.attendance_date == date(2026, 10, 6)
    assert early.record.teacher_id == teacher.id
    assert late.created is True
    assert late.record.status == AttendanceStatus.LATE
    assert late.record.teacher_id == teacher.id
    assert repeated.created is False
    assert repeated.record.id == early.record.id
    assert repeated.record.status == AttendanceStatus.PRESENT
    assert next_day.created is True
    assert next_day.record.attendance_date == date(2026, 10, 7)
    assert _count(session) == 3

    absent = mark_manual(
        session,
        actor=admin,
        student_id=alan.id,
        class_id=school_class.id,
        subject_id=math.id,
        status=AttendanceStatus.EXCUSED,
        now=datetime(2026, 10, 8, 8, 0, tzinfo=UTC),
    )
    assert absent.created is True
    assert absent.record.status == AttendanceStatus.EXCUSED
    assert absent.record.method == AttendanceMethod.MANUAL
    assert absent.record.confidence is None


def test_the_attendance_date_follows_the_configured_timezone(roster, monkeypatch) -> None:
    _client, session = roster
    monkeypatch.setenv("ATTENDANCE_TIMEZONE", "Asia/Kolkata")
    monkeypatch.setenv("ATTENDANCE_LATE_AFTER", "")
    get_settings.cache_clear()
    admin = _user(session, email="admin@school.edu", role=UserRole.ADMIN)
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    student = _student(session, email="ada@school.edu", roll="2026-014")
    school_class = _class(session, teacher)
    subject = _subject(session, school_class, teacher)
    _enroll(session, student, school_class)

    outcome = mark_from_frame(
        session,
        actor=admin,
        class_id=school_class.id,
        subject_id=subject.id,
        image_bytes=_png(),
        recognize=_recognize(student.id),
        now=datetime(2026, 10, 6, 20, 0, tzinfo=UTC),
    )

    assert outcome.record.attendance_date == date(2026, 10, 7)
    assert outcome.record.marked_at == datetime(2026, 10, 6, 20, 0, tzinfo=UTC)


def test_a_lesson_without_a_teacher_is_rejected(roster) -> None:
    _client, session = roster
    admin = _user(session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session, email="ada@school.edu", roll="2026-014")
    school_class = _class(session, None)
    subject = _subject(session, school_class, None)
    _enroll(session, student, school_class)

    with pytest.raises(TeacherNotIdentified):
        mark_from_frame(
            session,
            actor=admin,
            class_id=school_class.id,
            subject_id=subject.id,
            image_bytes=_png(),
            recognize=_recognize(student.id),
            now=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
        )
    assert _count(session) == 0


def test_a_lost_insert_race_returns_the_existing_row(roster, monkeypatch) -> None:
    _client, session = roster
    admin = _user(session, email="admin@school.edu", role=UserRole.ADMIN)
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    student = _student(session, email="ada@school.edu", roll="2026-014")
    school_class = _class(session, teacher)
    subject = _subject(session, school_class, teacher)
    _enroll(session, student, school_class)
    moment = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
    first = mark_from_frame(
        session,
        actor=admin,
        class_id=school_class.id,
        subject_id=subject.id,
        image_bytes=_png(),
        recognize=_recognize(student.id),
        now=moment,
    )
    real_find = attendance_service._find_mark
    calls = {"n": 0}

    def hiding(db, *, student_id: int, subject_id: int, day: date):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_find(db, student_id=student_id, subject_id=subject_id, day=day)

    monkeypatch.setattr(attendance_service, "_find_mark", hiding)
    second = mark_from_frame(
        session,
        actor=admin,
        class_id=school_class.id,
        subject_id=subject.id,
        image_bytes=_png(),
        recognize=_recognize(student.id),
        now=moment,
    )

    assert second.created is False
    assert second.record.id == first.record.id
    assert _count(session) == 1
    assert calls["n"] == 2


def test_the_database_rejects_a_second_row_for_the_same_day(roster) -> None:
    _client, session = roster
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    student = _student(session, email="ada@school.edu", roll="2026-014")
    school_class = _class(session, teacher)
    subject = _subject(session, school_class, teacher)
    shared = {
        "student_id": student.id,
        "class_id": school_class.id,
        "subject_id": subject.id,
        "teacher_id": teacher.id,
        "attendance_date": date(2026, 10, 6),
        "status": AttendanceStatus.PRESENT,
        "method": AttendanceMethod.FACE_RECOGNITION,
        "marked_at": datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
    }
    session.add(Attendance(**shared))
    session.commit()
    session.add(Attendance(**shared))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert _count(session) == 1


def test_service_rejections_leave_the_table_empty(roster) -> None:
    _client, session = roster
    admin = _user(session, email="admin@school.edu", role=UserRole.ADMIN)
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    student = _student(session, email="ada@school.edu", roll="2026-014")
    school_class = _class(session, teacher)
    subject = _subject(session, school_class, teacher)

    with pytest.raises(StudentNotRecognized):
        mark_from_frame(
            session,
            actor=admin,
            class_id=school_class.id,
            subject_id=subject.id,
            image_bytes=_png(),
            recognize=_recognize(None, matched=False),
            now=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
        )
    with pytest.raises(StudentNotInClass):
        mark_manual(
            session,
            actor=admin,
            student_id=student.id,
            class_id=school_class.id,
            subject_id=subject.id,
            status=AttendanceStatus.ABSENT,
            now=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
        )
    assert _count(session) == 0


def test_history_can_move_to_the_next_day_without_a_duplicate(roster) -> None:
    _client, session = roster
    admin = _user(session, email="admin@school.edu", role=UserRole.ADMIN)
    teacher = _teacher(session, email="grace@school.edu", code="T-014")
    student = _student(session, email="ada@school.edu", roll="2026-014")
    school_class = _class(session, teacher)
    subject = _subject(session, school_class, teacher)
    _enroll(session, student, school_class)
    first_day = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
    mark_manual(
        session,
        actor=admin,
        student_id=student.id,
        class_id=school_class.id,
        subject_id=subject.id,
        status=AttendanceStatus.LATE,
        now=first_day,
    )
    mark_manual(
        session,
        actor=admin,
        student_id=student.id,
        class_id=school_class.id,
        subject_id=subject.id,
        status=AttendanceStatus.PRESENT,
        now=first_day + timedelta(days=1),
    )

    rows, total = attendance_service.list_student_history(
        session,
        actor=admin,
        student_id=student.id,
        date_from=date(2026, 10, 6),
        date_to=date(2026, 10, 7),
        subject_id=subject.id,
        page=1,
        page_size=20,
    )

    assert total == 2
    assert [row.attendance_date for row in rows] == [date(2026, 10, 7), date(2026, 10, 6)]
    assert [row.status for row in rows] == [AttendanceStatus.PRESENT, AttendanceStatus.LATE]
