"""Face enrollment routes. These tests do not need MySQL or a camera."""

from datetime import date

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.faces import EMBEDDING_SIZE, FaceBox, prepare_embedding
from app.ai.recognition import EnrolledEmbedding
from app.services.face_recognition import recognize_frame
from app.auth.passwords import hash_password
from app.config import get_settings
from app.database.connection import get_db
from app.main import create_app
from app.models import (
    AuditLog,
    Base,
    Enrollment,
    EnrollmentStatus,
    FaceEncoding,
    SchoolClass,
    Student,
    Subject,
    Teacher,
    User,
    UserRole,
)
from app.routers.faces import MAX_IMAGE_BYTES, get_face_capture
from app.services.face_enrollment import MAX_ACTIVE_SAMPLES, TooManyFaceSamples

TEST_JWT_SECRET = "test-jwt-secret-must-be-32-bytes-or-more"
PASSWORD = "correct-horse-battery"
_EMBEDDING = [0.125] * EMBEDDING_SIZE
_FACE_FIELDS = {"student_id", "status", "sample_count", "samples"}
_SAMPLE_FIELDS = {"sample_id", "created_at"}


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
    """App wired to SQLite so face routes do not open MySQL."""
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


def _add_user(
    session,
    *,
    email: str,
    role: UserRole,
    active: bool = True,
    first_name: str = "Pat",
) -> User:
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


def _login(client: TestClient, email: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _headers(client, session, *, email: str, role: UserRole) -> dict[str, str]:
    _add_user(session, email=email, role=role, first_name="Admin" if role == UserRole.ADMIN else "Pat")
    return _bearer(_login(client, email))


def _student(session, *, email: str = "ada@school.edu", roll: str = "2026-014", active: bool = True) -> Student:
    user = _add_user(session, email=email, role=UserRole.STUDENT, active=active, first_name="Ada")
    student = Student(user_id=user.id, roll_number=roll)
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


def _teacher(session, *, email: str = "grace@school.edu", code: str = "T-014") -> Teacher:
    user = _add_user(session, email=email, role=UserRole.TEACHER, first_name="Grace")
    teacher = Teacher(user_id=user.id, employee_code=code)
    session.add(teacher)
    session.commit()
    session.refresh(teacher)
    return teacher


def _enroll(
    session,
    student: Student,
    teacher: Teacher,
    *,
    status: EnrollmentStatus = EnrollmentStatus.ACTIVE,
    as_subject_teacher: bool = False,
) -> None:
    school_class = SchoolClass(
        name="Grade 10",
        section="A" if not as_subject_teacher else "B",
        academic_year="2026-2027",
        class_teacher_id=None if as_subject_teacher else teacher.id,
        is_active=True,
    )
    session.add(school_class)
    session.flush()
    if as_subject_teacher:
        session.add(
            Subject(
                class_id=school_class.id,
                teacher_id=teacher.id,
                name="Mathematics",
                code="MATH",
                is_active=True,
            )
        )
    session.add(
        Enrollment(
            student_id=student.id,
            class_id=school_class.id,
            status=status,
            enrolled_on=date(2026, 8, 1),
        )
    )
    session.commit()


def _checkerboard(size: int = 96) -> np.ndarray:
    image = np.empty((size, size, 3), dtype=np.uint8)
    ys, xs = np.indices((size, size))
    dark = (ys + xs) % 2 == 0
    image[dark] = 80
    image[~dark] = 160
    return image


def _png(image: np.ndarray | None = None) -> bytes:
    ok, encoded = cv2.imencode(".png", _checkerboard() if image is None else image)
    assert ok
    return encoded.tobytes()


def _files(content: bytes | None = None, *, content_type: str = "image/png", name: str = "face.png"):
    return {"image": (name, _png() if content is None else content, content_type)}


def _use_capture(client, capture) -> None:
    client.app.dependency_overrides[get_face_capture] = lambda: capture


def _fixed_capture(_data: bytes) -> list[float]:
    return list(_EMBEDDING)


def _face(student_id: int) -> str:
    return f"/students/{student_id}/face"


def test_status_is_not_enrolled_until_a_sample_is_stored(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)
    session.add(
        FaceEncoding(
            student_id=student.id,
            encoding=list(_EMBEDDING),
            source_image_path=None,
            is_active=False,
        )
    )
    session.commit()

    response = client.get(_face(student.id), headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _FACE_FIELDS
    assert body["student_id"] == student.id
    assert body["status"] == "NOT_ENROLLED"
    assert body["sample_count"] == 0
    assert body["samples"] == []
    assert "encoding" not in response.text


def test_admin_can_store_multiple_samples_without_the_image(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)
    image = _png()
    seen: list[bytes] = []

    def capture(data: bytes) -> list[float]:
        seen.append(data)
        return [0.125 if len(seen) == 1 else 0.5] * EMBEDDING_SIZE

    _use_capture(client, capture)

    first = client.post(_face(student.id), headers=headers, files=_files(image))
    second = client.post(_face(student.id), headers=headers, files=_files(image))

    assert first.status_code == 201, first.text
    assert first.headers["location"] == _face(student.id)
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["status"] == "ENROLLED"
    assert body["sample_count"] == 2
    assert [set(sample) for sample in body["samples"]] == [_SAMPLE_FIELDS, _SAMPLE_FIELDS]
    assert seen == [image, image]
    assert image not in first.content
    assert "encoding" not in second.text
    assert "source_image" not in second.text

    session.expire_all()
    rows = session.scalars(select(FaceEncoding).order_by(FaceEncoding.id)).all()
    assert [row.source_image_path for row in rows] == [None, None]
    assert [row.is_active for row in rows] == [True, True]
    assert rows[0].encoding[0] == 0.125
    assert rows[1].encoding[0] == 0.5
    assert all(len(row.encoding) == EMBEDDING_SIZE for row in rows)

    audits = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [audit.action for audit in audits] == ["face.enrolled", "face.enrolled"]
    assert [audit.details for audit in audits] == [{"sample_count": 1}, {"sample_count": 2}]
    assert all("encoding" not in audit.details for audit in audits)


def test_replace_removes_previous_samples(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)
    _use_capture(client, _fixed_capture)
    assert client.post(_face(student.id), headers=headers, files=_files()).status_code == 201
    assert client.post(_face(student.id), headers=headers, files=_files()).status_code == 201

    _use_capture(client, lambda _data: [0.75] * EMBEDDING_SIZE)
    response = client.put(_face(student.id), headers=headers, files=_files())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ENROLLED"
    assert body["sample_count"] == 1
    session.expire_all()
    rows = session.scalars(select(FaceEncoding)).all()
    assert len(rows) == 1
    assert rows[0].encoding[0] == 0.75
    assert rows[0].source_image_path is None
    assert rows[0].id == body["samples"][0]["sample_id"]
    audit = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()[-1]
    assert audit.action == "face.replaced"
    assert audit.details == {"sample_count": 1}


def test_a_student_cannot_have_more_than_the_sample_limit(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)
    _use_capture(client, _fixed_capture)
    for _ in range(MAX_ACTIVE_SAMPLES):
        assert client.post(_face(student.id), headers=headers, files=_files()).status_code == 201

    rejected = client.post(_face(student.id), headers=headers, files=_files())

    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"] == TooManyFaceSamples.detail
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(FaceEncoding)) == MAX_ACTIVE_SAMPLES

    replaced = client.put(_face(student.id), headers=headers, files=_files())
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["sample_count"] == 1
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(FaceEncoding)) == 1


def test_quality_failures_do_not_store_a_sample(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)

    def capture(data: bytes) -> list[float]:
        return prepare_embedding(
            data,
            detect=lambda image: [FaceBox(0, image.shape[1], image.shape[0], 0)],
            encode=lambda _image, _box: [0.25] * EMBEDDING_SIZE,
        )

    _use_capture(client, capture)
    dark = np.full((96, 96, 3), 10, dtype=np.uint8)
    response = client.post(_face(student.id), headers=headers, files=_files(_png(dark)))

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Image is too dark"
    assert session.scalar(select(func.count()).select_from(FaceEncoding)) == 0


def test_zero_and_multiple_faces_are_rejected_by_the_endpoint(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)

    def pipeline(boxes: list[FaceBox]):
        def capture(data: bytes) -> list[float]:
            return prepare_embedding(data, detect=lambda _image: boxes, encode=lambda _image, _box: _EMBEDDING)

        return capture

    _use_capture(client, pipeline([]))
    missing = client.post(_face(student.id), headers=headers, files=_files())
    assert missing.status_code == 422
    assert missing.json()["detail"] == "No face detected"

    box = FaceBox(0, 96, 96, 0)
    _use_capture(client, pipeline([box, box]))
    crowded = client.post(_face(student.id), headers=headers, files=_files())
    assert crowded.status_code == 422
    assert crowded.json()["detail"] == "Image must contain exactly one face"
    assert session.scalar(select(func.count()).select_from(FaceEncoding)) == 0


def test_unreadable_non_image_and_oversized_uploads_are_rejected(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)

    corrupt = client.post(
        _face(student.id),
        headers=headers,
        files=_files(b"not-an-image", content_type="image/png"),
    )
    text = client.post(
        _face(student.id),
        headers=headers,
        files=_files(b"hello", content_type="text/plain", name="note.txt"),
    )
    empty = client.post(
        _face(student.id),
        headers=headers,
        files=_files(b"", content_type="image/png"),
    )
    huge = client.post(
        _face(student.id),
        headers=headers,
        files=_files(b"\x00" * (MAX_IMAGE_BYTES + 1), content_type="image/jpeg", name="big.jpg"),
    )

    assert corrupt.status_code == 422
    assert corrupt.json()["detail"] == "Image could not be read"
    assert text.status_code == 422
    assert text.json()["detail"] == "Upload an image file"
    assert empty.status_code == 422
    assert empty.json()["detail"] == "Image file is empty"
    assert huge.status_code == 422
    assert huge.json()["detail"] == "Image file is too large"
    assert session.scalar(select(func.count()).select_from(FaceEncoding)) == 0


def test_a_short_embedding_is_not_stored(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)
    _use_capture(client, lambda _data: [0.1, 0.2])

    response = client.post(_face(student.id), headers=headers, files=_files())

    assert response.status_code == 422
    assert response.json()["detail"] == "Could not generate a face embedding"
    assert session.scalar(select(func.count()).select_from(FaceEncoding)) == 0


def test_assigned_class_and_subject_teachers_can_enroll(roster_client) -> None:
    client, session = roster_client
    student = _student(session)
    class_teacher = _teacher(session)
    _enroll(session, student, class_teacher)
    headers = _bearer(_login(client, "grace@school.edu"))
    _use_capture(client, _fixed_capture)

    response = client.post(_face(student.id), headers=headers, files=_files())
    status = client.get(_face(student.id), headers=headers)

    assert response.status_code == 201, response.text
    assert status.status_code == 200
    assert status.json()["sample_count"] == 1

    other = _student(session, email="alan@school.edu", roll="2026-015")
    subject_teacher = _teacher(session, email="alan-teacher@school.edu", code="T-015")
    _enroll(session, other, subject_teacher, as_subject_teacher=True)
    subject_headers = _bearer(_login(client, "alan-teacher@school.edu"))
    enrolled = client.post(_face(other.id), headers=subject_headers, files=_files())
    assert enrolled.status_code == 201, enrolled.text


def test_teachers_cannot_enroll_students_outside_their_classes(roster_client) -> None:
    client, session = roster_client
    student = _student(session)
    teacher = _teacher(session)
    outsider = _teacher(session, email="outsider@school.edu", code="T-099")
    _enroll(session, student, teacher)
    headers = _bearer(_login(client, "outsider@school.edu"))
    _use_capture(client, _fixed_capture)

    denied = client.post(_face(student.id), headers=headers, files=_files())
    hidden = client.get(_face(student.id), headers=headers)

    assert denied.status_code == 404
    assert denied.json()["detail"] == "Student not found"
    assert hidden.status_code == 404
    assert session.scalar(select(func.count()).select_from(FaceEncoding)) == 0
    assert outsider.id != teacher.id


def test_a_withdrawn_student_is_hidden_from_the_teacher(roster_client) -> None:
    client, session = roster_client
    admin = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)
    teacher = _teacher(session)
    _enroll(session, student, teacher, status=EnrollmentStatus.WITHDRAWN)
    teacher_headers = _bearer(_login(client, "grace@school.edu"))
    _use_capture(client, _fixed_capture)

    denied = client.post(_face(student.id), headers=teacher_headers, files=_files())
    allowed = client.post(_face(student.id), headers=admin, files=_files())

    assert denied.status_code == 404
    assert allowed.status_code == 201, allowed.text


def test_inactive_students_cannot_be_enrolled(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session, active=False)
    _use_capture(client, _fixed_capture)

    status = client.get(_face(student.id), headers=headers)
    rejected = client.post(_face(student.id), headers=headers, files=_files())

    assert status.status_code == 200
    assert status.json()["status"] == "NOT_ENROLLED"
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "Student is not active"
    assert session.scalar(select(func.count()).select_from(FaceEncoding)) == 0


def test_students_and_anonymous_callers_cannot_enroll(roster_client) -> None:
    client, session = roster_client
    student = _student(session)
    headers = _bearer(_login(client, "ada@school.edu"))
    path = _face(student.id)

    denied = client.get(path, headers=headers)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Admin or teacher access required"
    assert client.get(path).status_code == 401

    for method in (client.post, client.put):
        rejected = method(path, headers=headers, files=_files())
        assert rejected.status_code == 403
        assert rejected.json()["detail"] == "Admin or teacher access required"
        assert method(path, files=_files()).status_code == 401


def test_a_teacher_without_a_profile_cannot_enroll(roster_client) -> None:
    client, session = roster_client
    student = _student(session)
    _add_user(session, email="new-teacher@school.edu", role=UserRole.TEACHER)
    headers = _bearer(_login(client, "new-teacher@school.edu"))

    response = client.get(_face(student.id), headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Teacher profile not found"


def _recognize_with_counting_loader(session, student_id: int, loads: dict[str, int]):
    def load(_db):
        loads["n"] += 1
        return [EnrolledEmbedding(student_id=student_id, embedding=tuple(_EMBEDDING))]

    return recognize_frame(
        _png(),
        db=session,
        detect=lambda _image: [FaceBox(0, 8, 8, 0)],
        encode=lambda _image, _box: list(_EMBEDDING),
        load_embeddings=load,
    )


def test_saving_a_face_clears_the_recognition_gallery(roster_client, monkeypatch) -> None:
    monkeypatch.setenv("FACE_EMBEDDING_CACHE_SECONDS", "3600")
    get_settings.cache_clear()
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)
    _use_capture(client, _fixed_capture)
    loads = {"n": 0}

    assert _recognize_with_counting_loader(session, student.id, loads).matched is True
    saved = client.post(_face(student.id), headers=headers, files=_files())

    assert saved.status_code == 201, saved.text
    assert _recognize_with_counting_loader(session, student.id, loads).student_id == student.id
    assert loads["n"] == 2


def test_a_rejected_face_upload_keeps_the_recognition_gallery(roster_client, monkeypatch) -> None:
    monkeypatch.setenv("FACE_EMBEDDING_CACHE_SECONDS", "3600")
    get_settings.cache_clear()
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)
    student = _student(session)
    loads = {"n": 0}

    def capture(data: bytes) -> list[float]:
        return prepare_embedding(
            data,
            detect=lambda image: [FaceBox(0, image.shape[1], image.shape[0], 0)],
            encode=lambda _image, _box: list(_EMBEDDING),
        )

    _use_capture(client, capture)
    assert _recognize_with_counting_loader(session, student.id, loads).matched is True
    dark = np.full((96, 96, 3), 10, dtype=np.uint8)
    rejected = client.post(_face(student.id), headers=headers, files=_files(_png(dark)))

    assert rejected.status_code == 422, rejected.text
    assert _recognize_with_counting_loader(session, student.id, loads).matched is True
    assert loads["n"] == 1


def test_missing_student_is_not_found(roster_client) -> None:
    client, session = roster_client
    headers = _headers(client, session, email="admin@school.edu", role=UserRole.ADMIN)

    response = client.get("/students/999/face", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Student not found"
