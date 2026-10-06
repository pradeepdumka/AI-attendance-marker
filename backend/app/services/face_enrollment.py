"""Store face embeddings for a student.

An admin may enroll any student. A teacher may enroll a student who is
actively in a class that teacher is assigned to, either as class teacher
or as the teacher of an active subject. Each call stores one embedding.
The captured image is not written to disk or to the database.

Adding a sample keeps earlier active samples. Replacing the enrollment
removes those rows and stores the new embedding in their place. A saved
sample clears the in-memory gallery used by face recognition.
"""

from collections.abc import Callable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.ai.faces import compact_embedding, prepare_embedding
from app.models.audit_log import AuditLog
from app.models.class_model import SchoolClass
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.face_encoding import FaceEncoding
from app.models.student import Student
from app.models.subject import Subject
from app.models.user import User, UserRole
from app.services.face_recognition import invalidate_embedding_cache
from app.services.teachers import get_teacher_for_user

MAX_ACTIVE_SAMPLES = 5

FaceCapture = Callable[[bytes], Sequence[float]]


class FaceEnrollmentError(Exception):
    """Expected enrollment failure. The router chooses the HTTP response."""


class StudentNotFound(FaceEnrollmentError):
    """No student has this id, or this teacher is not assigned to them."""


class TeacherProfileNotFound(FaceEnrollmentError):
    """The teacher account has no staff profile."""


class StudentNotActive(FaceEnrollmentError):
    """The student account cannot sign in."""


class TooManyFaceSamples(FaceEnrollmentError):
    """The student already has the maximum number of active samples."""

    detail = f"A student can have at most {MAX_ACTIVE_SAMPLES} active face samples"


class FaceEnrollmentNotAllowed(FaceEnrollmentError):
    """The caller is not an admin or a teacher."""


def enroll_face(
    db: Session,
    *,
    actor: User,
    student_id: int,
    image_bytes: bytes,
    ip_address: str | None = None,
    capture: FaceCapture = prepare_embedding,
) -> list[FaceEncoding]:
    """Validate one frame and store another active embedding for the student."""
    student = _authorize(db, actor, student_id)
    _require_active(student)
    existing = _active_samples(db, student.id)
    if len(existing) >= MAX_ACTIVE_SAMPLES:
        raise TooManyFaceSamples
    embedding = _embedding_from(capture, image_bytes)
    _save_sample(
        db,
        actor_id=actor.id,
        student_id=student.id,
        embedding=embedding,
        action="face.enrolled",
        sample_count=len(existing) + 1,
        ip_address=ip_address,
        replace=False,
    )
    return _active_samples(db, student.id)


def replace_face_enrollment(
    db: Session,
    *,
    actor: User,
    student_id: int,
    image_bytes: bytes,
    ip_address: str | None = None,
    capture: FaceCapture = prepare_embedding,
) -> list[FaceEncoding]:
    """Replace every stored sample for this student with one new embedding."""
    student = _authorize(db, actor, student_id)
    _require_active(student)
    embedding = _embedding_from(capture, image_bytes)
    _save_sample(
        db,
        actor_id=actor.id,
        student_id=student.id,
        embedding=embedding,
        action="face.replaced",
        sample_count=1,
        ip_address=ip_address,
        replace=True,
    )
    return _active_samples(db, student.id)


def get_face_enrollment(
    db: Session,
    *,
    actor: User,
    student_id: int,
) -> tuple[int, list[FaceEncoding]]:
    """Return the student id and active face samples."""
    student = _authorize(db, actor, student_id)
    return student.id, _active_samples(db, student.id)


def _embedding_from(capture: FaceCapture, image_bytes: bytes) -> list[float]:
    """Run capture, then drop the image bytes.

    `compact_embedding` checks the length again so a stand-in capture cannot
    store an image or a short vector.
    """
    return compact_embedding(capture(image_bytes))


def _save_sample(
    db: Session,
    *,
    actor_id: int,
    student_id: int,
    embedding: list[float],
    action: str,
    sample_count: int,
    ip_address: str | None,
    replace: bool,
) -> None:
    try:
        if replace:
            db.execute(
                delete(FaceEncoding).where(FaceEncoding.student_id == student_id),
                execution_options={"synchronize_session": "fetch"},
            )
        db.add(
            FaceEncoding(
                student_id=student_id,
                encoding=embedding,
                source_image_path=None,
                is_active=True,
            )
        )
        _audit(
            db,
            actor_id=actor_id,
            action=action,
            student_id=student_id,
            details={"sample_count": sample_count},
            ip_address=ip_address,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    invalidate_embedding_cache()


def _authorize(db: Session, actor: User, student_id: int) -> Student:
    if actor.role == UserRole.ADMIN:
        return _require_student(db, student_id)
    if actor.role != UserRole.TEACHER:
        raise FaceEnrollmentNotAllowed
    teacher = get_teacher_for_user(db, actor.id)
    if teacher is None:
        raise TeacherProfileNotFound
    student = _require_student(db, student_id)
    if not _teacher_teaches_student(db, teacher.id, student.id):
        raise StudentNotFound
    return student


def _require_active(student: Student) -> None:
    if not student.user.is_active:
        raise StudentNotActive


def _require_student(db: Session, student_id: int) -> Student:
    student = db.scalar(
        select(Student).where(Student.id == student_id).options(joinedload(Student.user))
    )
    if student is None:
        raise StudentNotFound
    return student


def _teacher_teaches_student(db: Session, teacher_id: int, student_id: int) -> bool:
    class_ids = select(Enrollment.class_id).where(
        Enrollment.student_id == student_id,
        Enrollment.status == EnrollmentStatus.ACTIVE,
    )
    homeroom_id = db.scalar(
        select(SchoolClass.id).where(
            SchoolClass.id.in_(class_ids),
            SchoolClass.class_teacher_id == teacher_id,
            SchoolClass.is_active.is_(True),
        )
    )
    if homeroom_id is not None:
        return True
    subject_id = db.scalar(
        select(Subject.id)
        .join(SchoolClass, Subject.class_id == SchoolClass.id)
        .where(
            Subject.class_id.in_(class_ids),
            Subject.teacher_id == teacher_id,
            Subject.is_active.is_(True),
            SchoolClass.is_active.is_(True),
        )
    )
    return subject_id is not None


def _active_samples(db: Session, student_id: int) -> list[FaceEncoding]:
    return list(
        db.scalars(
            select(FaceEncoding)
            .where(
                FaceEncoding.student_id == student_id,
                FaceEncoding.is_active.is_(True),
            )
            .order_by(FaceEncoding.id)
        ).all()
    )


def _audit(
    db: Session,
    *,
    actor_id: int,
    action: str,
    student_id: int,
    details: dict[str, object],
    ip_address: str | None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type="student",
            entity_id=str(student_id),
            details=details,
            ip_address=ip_address,
        )
    )

