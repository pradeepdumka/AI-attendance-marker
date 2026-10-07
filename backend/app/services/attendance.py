"""Mark attendance from a camera frame and read the stored rows.

A frame is recognized, then the student, class, subject, and teacher are
checked. One student has one row per subject per date. The service returns
the existing row instead of inserting another. The database unique
constraint stops a second insert if two requests pass that check together.
This module does not store the camera frame.
"""

from __future__ import annotations

import logging
from calendar import monthrange
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

import app.models  # noqa: F401  # register every mapper before writing attendance
from app.ai.recognition import RecognitionResult
from app.config import get_settings
from app.models.attendance import Attendance, AttendanceMethod, AttendanceStatus
from app.models.attendance_session import AttendanceSession, SessionStatus
from app.models.audit_log import AuditLog
from app.models.base import utcnow
from app.models.class_model import SchoolClass
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.student import Student
from app.models.subject import Subject
from app.models.teacher import Teacher
from app.models.user import User, UserRole
from app.services.academics import enrolled_class
from app.services.face_recognition import recognize_frame
from app.services.teachers import get_teacher_for_user

logger = logging.getLogger(__name__)

Recognizer = Callable[..., RecognitionResult]


class AttendanceError(Exception):
    """Expected attendance failure. The router chooses the HTTP response."""


class AttendanceNotAllowed(AttendanceError):
    """The caller is not an admin or a teacher."""


class TeacherProfileNotFound(AttendanceError):
    """The teacher account has no staff profile."""


class ClassNotFound(AttendanceError):
    """No class has this id, or this teacher is not assigned to it."""


class SubjectNotFound(AttendanceError):
    """No subject has this id in this class, or this teacher cannot mark it."""


class StudentNotFound(AttendanceError):
    """No student has this id, or this teacher cannot read them."""


class StudentNotActive(AttendanceError):
    """The student account cannot be marked present."""


class StudentNotInClass(AttendanceError):
    """The student has no active enrollment in this class."""


class ClassNotActive(AttendanceError):
    """The class is not open for attendance."""


class SubjectNotActive(AttendanceError):
    """The subject is not open for attendance."""


class TeacherNotIdentified(AttendanceError):
    """The lesson has no subject teacher and no class teacher."""


class StudentNotRecognized(AttendanceError):
    """The frame did not match an enrolled student."""


class InvalidDateRange(AttendanceError):
    """The history range ends before it starts."""


class SessionNotFound(AttendanceError):
    """This teacher has no attendance session with that id."""


class AttendanceMark:
    """A stored row and whether this call inserted it."""

    def __init__(self, record: Attendance, *, created: bool) -> None:
        self.record = record
        self.created = created


class AttendanceSessionStart:
    """A stored session and whether this call inserted it."""

    def __init__(self, record: AttendanceSession, *, created: bool) -> None:
        self.record = record
        self.created = created


# A teacher who is only a subject teacher must not read another subject's rows.
MarkRestrict = tuple[set[int], set[int]] | None


def mark_from_frame(
    db: Session,
    *,
    actor: User,
    class_id: int,
    subject_id: int,
    image_bytes: bytes,
    ip_address: str | None = None,
    recognize: Recognizer = recognize_frame,
    now: datetime | None = None,
) -> AttendanceMark:
    """Recognize one frame and mark that student for this lesson today.

    The status is PRESENT, or LATE when the local check-in is at or after
    the configured cutoff. A second frame for the same student, subject,
    and date does not insert another row.
    """
    result = recognize(image_bytes, db=db)
    if not result.matched or result.student_id is None:
        logger.info(
            "Attendance frame did not match a student: class_id=%s subject_id=%s",
            class_id,
            subject_id,
        )
        raise StudentNotRecognized
    local = local_now(now)
    return _mark_student(
        db,
        actor=actor,
        student_id=result.student_id,
        class_id=class_id,
        subject_id=subject_id,
        status=_status_for_check_in(local),
        method=AttendanceMethod.FACE_RECOGNITION,
        confidence=result.confidence,
        check_in=local,
        ip_address=ip_address,
    )


def mark_manual(
    db: Session,
    *,
    actor: User,
    student_id: int,
    class_id: int,
    subject_id: int,
    status: AttendanceStatus,
    ip_address: str | None = None,
    now: datetime | None = None,
) -> AttendanceMark:
    """Record a staff mark for a known student without a camera frame.

    PRESENT, ABSENT, LATE, and EXCUSED all use the same duplicate rule as
    a face check-in. An existing row for today is left unchanged.
    """
    return _mark_student(
        db,
        actor=actor,
        student_id=student_id,
        class_id=class_id,
        subject_id=subject_id,
        status=status,
        method=AttendanceMethod.MANUAL,
        confidence=None,
        check_in=local_now(now),
        ip_address=ip_address,
    )


def list_today(
    db: Session,
    *,
    actor: User,
    class_id: int | None,
    subject_id: int | None,
    page: int,
    page_size: int,
    now: datetime | None = None,
) -> tuple[list[Attendance], int]:
    """Return today's marks the caller is allowed to see."""
    day = local_now(now).date()
    restrict = _prepare_staff_read(db, actor, class_id, subject_id)
    return _list_marks(
        db,
        restrict=restrict,
        class_id=class_id,
        subject_id=subject_id,
        student_id=None,
        day=day,
        date_from=None,
        date_to=None,
        page=page,
        page_size=page_size,
        newest_first=False,
    )


def list_class_attendance(
    db: Session,
    *,
    actor: User,
    class_id: int,
    subject_id: int | None,
    day: date | None,
    page: int,
    page_size: int,
    now: datetime | None = None,
) -> tuple[list[Attendance], int]:
    """Return stored marks for one class. Unmarked students are not invented."""
    _require_visible_class(db, actor, class_id)
    if subject_id is not None:
        _require_subject_in_class(db, class_id, subject_id)
        if actor.role == UserRole.TEACHER:
            _require_readable_subject(db, actor, subject_id, class_id)
    on = day if day is not None else local_now(now).date()
    restrict = _teacher_restrict(db, actor) if actor.role == UserRole.TEACHER else None
    return _list_marks(
        db,
        restrict=restrict,
        class_id=class_id,
        subject_id=subject_id,
        student_id=None,
        day=on,
        date_from=None,
        date_to=None,
        page=page,
        page_size=page_size,
        newest_first=False,
    )


def list_student_history(
    db: Session,
    *,
    actor: User,
    student_id: int,
    date_from: date | None,
    date_to: date | None,
    subject_id: int | None,
    page: int,
    page_size: int,
) -> tuple[list[Attendance], int]:
    """Return one student's marks, newest date first."""
    _require_visible_student(db, actor, student_id)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidDateRange
    if subject_id is not None and actor.role == UserRole.TEACHER:
        _require_readable_subject(db, actor, subject_id, None)
    restrict = _teacher_restrict(db, actor) if actor.role == UserRole.TEACHER else None
    return _list_marks(
        db,
        restrict=restrict,
        class_id=None,
        subject_id=subject_id,
        student_id=student_id,
        day=None,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
        newest_first=True,
    )


def student_attendance_counts(
    db: Session,
    *,
    actor: User,
    student_id: int,
    date_from: date | None,
    date_to: date | None,
    subject_id: int | None,
) -> dict[AttendanceStatus, int]:
    """Count the signed-in student's own marks.

    A subject filter that is not this student's still returns zeros.
    It does not reveal whether that subject exists.
    """
    student = _require_student_self(db, actor, student_id)
    _require_date_order(date_from, date_to)
    return _status_counts(
        db,
        student_id=student.id,
        subject_id=subject_id,
        date_from=date_from,
        date_to=date_to,
    )


def student_attendance_by_subject(
    db: Session,
    *,
    actor: User,
    student_id: int,
    date_from: date | None,
    date_to: date | None,
) -> list[tuple[Subject, dict[AttendanceStatus, int]]]:
    """Return this student's totals for each visible subject.

    Current active subjects are included even with no marks. A subject
    from an earlier class is included only when this student has a mark.
    """
    student = _require_student_self(db, actor, student_id)
    _require_date_order(date_from, date_to)
    grouped = _grouped_counts(
        db,
        student_id=student.id,
        subject_id=None,
        date_from=date_from,
        date_to=date_to,
        column=Attendance.subject_id,
    )
    subjects = _subjects_for_student(db, student.id, set(grouped))
    return [(subject, grouped.get(subject.id, _zero_counts())) for subject in subjects]


def student_month_counts(
    db: Session,
    *,
    actor: User,
    student_id: int,
    year: int,
    month: int,
    subject_id: int | None,
) -> dict[AttendanceStatus, int]:
    """Count the signed-in student's marks in one calendar month."""
    start, end = _month_span(year, month)
    return student_attendance_counts(
        db,
        actor=actor,
        student_id=student_id,
        date_from=start,
        date_to=end,
        subject_id=subject_id,
    )


def student_attendance_calendar(
    db: Session,
    *,
    actor: User,
    student_id: int,
    year: int,
    month: int,
    subject_id: int | None,
) -> tuple[dict[AttendanceStatus, int], list[tuple[date, dict[AttendanceStatus, int]]]]:
    """Return the month totals and each date that has a mark."""
    student = _require_student_self(db, actor, student_id)
    start, end = _month_span(year, month)
    totals = _status_counts(
        db,
        student_id=student.id,
        subject_id=subject_id,
        date_from=start,
        date_to=end,
    )
    grouped = _grouped_counts(
        db,
        student_id=student.id,
        subject_id=subject_id,
        date_from=start,
        date_to=end,
        column=Attendance.attendance_date,
    )
    days = [(_as_date(day), counts) for day, counts in grouped.items()]
    days.sort(key=lambda item: item[0])
    return totals, days


def list_history(
    db: Session,
    *,
    actor: User,
    class_id: int | None,
    subject_id: int | None,
    date_from: date | None,
    date_to: date | None,
    page: int,
    page_size: int,
) -> tuple[list[Attendance], int]:
    """Return attendance in a date range, newest date first."""
    _require_date_order(date_from, date_to)
    restrict = _prepare_staff_read(db, actor, class_id, subject_id)
    return _list_marks(
        db,
        restrict=restrict,
        class_id=class_id,
        subject_id=subject_id,
        student_id=None,
        day=None,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
        newest_first=True,
    )


def attendance_statistics(
    db: Session,
    *,
    actor: User,
    class_id: int | None,
    subject_id: int | None,
    date_from: date | None,
    date_to: date | None,
) -> dict[AttendanceStatus, int]:
    """Count each status for attendance the caller is allowed to read."""
    _require_date_order(date_from, date_to)
    restrict = _prepare_staff_read(db, actor, class_id, subject_id)
    rows = db.execute(
        _filtered(
            select(Attendance.status, func.count(Attendance.id)),
            restrict=restrict,
            class_id=class_id,
            subject_id=subject_id,
            student_id=None,
            day=None,
            date_from=date_from,
            date_to=date_to,
        ).group_by(Attendance.status)
    ).all()
    counts = {status: 0 for status in AttendanceStatus}
    for status, count in rows:
        key = status if isinstance(status, AttendanceStatus) else AttendanceStatus(status)
        counts[key] = int(count)
    return counts


def export_attendance(
    db: Session,
    *,
    actor: User,
    class_id: int | None,
    subject_id: int | None,
    date_from: date | None,
    date_to: date | None,
    limit: int = 5000,
) -> list[Attendance]:
    """Return attendance rows for a CSV report, newest date first."""
    _require_date_order(date_from, date_to)
    restrict = _prepare_staff_read(db, actor, class_id, subject_id)
    rows = db.scalars(
        _filtered(
            select(Attendance),
            restrict=restrict,
            class_id=class_id,
            subject_id=subject_id,
            student_id=None,
            day=None,
            date_from=date_from,
            date_to=date_to,
        )
        .options(
            joinedload(Attendance.student).joinedload(Student.user),
            joinedload(Attendance.school_class),
            joinedload(Attendance.subject),
        )
        .order_by(Attendance.attendance_date.desc(), Attendance.id.desc())
        .limit(limit)
    ).unique().all()
    return list(rows)


def start_attendance_session(
    db: Session,
    *,
    actor: User,
    class_id: int,
    subject_id: int,
    ip_address: str | None = None,
    now: datetime | None = None,
) -> AttendanceSessionStart:
    """Open today's session for a class and subject assigned to this teacher.

    The same lesson and date returns the existing row. A closed session
    opens again. A teacher who is not assigned does not create a row.
    """
    teacher = _require_session_teacher(db, actor)
    _lesson(db, actor, class_id, subject_id)
    day = local_now(now).date()
    existing = _find_session(
        db,
        teacher_id=teacher.id,
        class_id=class_id,
        subject_id=subject_id,
        day=day,
    )
    if existing is not None and existing.status == SessionStatus.OPEN:
        return AttendanceSessionStart(existing, created=False)
    if existing is not None:
        existing.status = SessionStatus.OPEN
        existing.started_at = utcnow()
        existing.ended_at = None
        existing.updated_at = utcnow()
        _audit_session(
            db,
            actor_id=actor.id,
            session_id=existing.id,
            class_id=class_id,
            subject_id=subject_id,
            day=day,
            action="attendance.session_started",
            ip_address=ip_address,
        )
        db.commit()
        db.refresh(existing)
        return AttendanceSessionStart(existing, created=False)
    record = AttendanceSession(
        teacher_id=teacher.id,
        class_id=class_id,
        subject_id=subject_id,
        session_date=day,
        status=SessionStatus.OPEN,
        started_at=utcnow(),
    )
    try:
        db.add(record)
        db.flush()
        _audit_session(
            db,
            actor_id=actor.id,
            session_id=record.id,
            class_id=class_id,
            subject_id=subject_id,
            day=day,
            action="attendance.session_started",
            ip_address=ip_address,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if not _is_duplicate_session(exc):
            raise
        raced = _find_session(
            db,
            teacher_id=teacher.id,
            class_id=class_id,
            subject_id=subject_id,
            day=day,
        )
        if raced is None:
            raise
        return AttendanceSessionStart(raced, created=False)
    except Exception:
        db.rollback()
        raise
    db.refresh(record)
    return AttendanceSessionStart(record, created=True)


def close_attendance_session(
    db: Session,
    *,
    actor: User,
    session_id: int,
    ip_address: str | None = None,
) -> AttendanceSession:
    """Close a session that belongs to this teacher. Closing twice is a no-op."""
    teacher = _require_session_teacher(db, actor)
    record = db.get(AttendanceSession, session_id)
    if record is None or record.teacher_id != teacher.id:
        raise SessionNotFound
    if record.status == SessionStatus.CLOSED:
        return record
    record.status = SessionStatus.CLOSED
    record.ended_at = utcnow()
    record.updated_at = utcnow()
    _audit_session(
        db,
        actor_id=actor.id,
        session_id=record.id,
        class_id=record.class_id,
        subject_id=record.subject_id,
        day=record.session_date,
        action="attendance.session_closed",
        ip_address=ip_address,
    )
    db.commit()
    db.refresh(record)
    return record


def local_now(now: datetime | None = None) -> datetime:
    """Return the check-in moment in the configured attendance timezone."""
    moment = now if now is not None else utcnow()
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("now must be a timezone-aware datetime")
    return moment.astimezone(ZoneInfo(get_settings().attendance_timezone))


def _mark_student(
    db: Session,
    *,
    actor: User,
    student_id: int,
    class_id: int,
    subject_id: int,
    status: AttendanceStatus,
    method: AttendanceMethod,
    confidence: float | None,
    check_in: datetime,
    ip_address: str | None,
) -> AttendanceMark:
    lesson = _lesson(db, actor, class_id, subject_id)
    student = _require_enrolled_student(db, student_id, class_id)
    day = check_in.date()
    existing = _find_mark(db, student_id=student.id, subject_id=subject_id, day=day)
    if existing is not None:
        logger.info(
            "Attendance already marked: attendance_id=%s student_id=%s subject_id=%s date=%s",
            existing.id,
            student.id,
            subject_id,
            day.isoformat(),
        )
        return AttendanceMark(existing, created=False)
    record = Attendance(
        student_id=student.id,
        class_id=class_id,
        subject_id=subject_id,
        teacher_id=lesson.teacher_id,
        attendance_date=day,
        status=status,
        method=method,
        confidence=_confidence_score(confidence),
        marked_by_id=actor.id,
        marked_at=check_in.astimezone(ZoneInfo("UTC")),
    )
    try:
        db.add(record)
        db.flush()
        _audit(
            db,
            actor_id=actor.id,
            attendance_id=record.id,
            student_id=student.id,
            class_id=class_id,
            subject_id=subject_id,
            status=status,
            day=day,
            method=method,
            ip_address=ip_address,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if not _is_duplicate_mark(exc):
            raise
        raced = _find_mark(db, student_id=student.id, subject_id=subject_id, day=day)
        if raced is None:
            raise
        logger.info(
            "Attendance insert matched an existing row: attendance_id=%s student_id=%s subject_id=%s date=%s",
            raced.id,
            student.id,
            subject_id,
            day.isoformat(),
        )
        return AttendanceMark(raced, created=False)
    except Exception:
        db.rollback()
        raise
    logger.info(
        "Attendance marked: attendance_id=%s student_id=%s class_id=%s subject_id=%s status=%s date=%s method=%s",
        record.id,
        student.id,
        class_id,
        subject_id,
        status.value,
        day.isoformat(),
        method.value,
    )
    return AttendanceMark(_load_mark(db, record.id), created=True)


class _Lesson:
    def __init__(self, teacher_id: int) -> None:
        self.teacher_id = teacher_id


def _lesson(db: Session, actor: User, class_id: int, subject_id: int) -> _Lesson:
    school_class = _require_class(db, class_id)
    subject = _require_subject_in_class(db, class_id, subject_id)
    if not school_class.is_active:
        raise ClassNotActive
    if not subject.is_active:
        raise SubjectNotActive
    actor_teacher = _actor_teacher(db, actor, school_class, subject)
    teacher_id = subject.teacher_id or school_class.class_teacher_id
    if teacher_id is None and actor_teacher is not None:
        teacher_id = actor_teacher.id
    if teacher_id is None:
        raise TeacherNotIdentified
    return _Lesson(teacher_id)


def _actor_teacher(
    db: Session,
    actor: User,
    school_class: SchoolClass,
    subject: Subject,
) -> Teacher | None:
    if actor.role == UserRole.ADMIN:
        return None
    if actor.role != UserRole.TEACHER:
        raise AttendanceNotAllowed
    teacher = get_teacher_for_user(db, actor.id)
    if teacher is None:
        raise TeacherProfileNotFound
    if subject.teacher_id != teacher.id and school_class.class_teacher_id != teacher.id:
        raise SubjectNotFound
    return teacher


def _require_enrolled_student(db: Session, student_id: int, class_id: int) -> Student:
    student = db.scalar(
        select(Student).where(Student.id == student_id).options(joinedload(Student.user))
    )
    if student is None:
        raise StudentNotFound
    if not student.user.is_active:
        raise StudentNotActive
    enrollment_id = db.scalar(
        select(Enrollment.id).where(
            Enrollment.student_id == student.id,
            Enrollment.class_id == class_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
    )
    if enrollment_id is None:
        raise StudentNotInClass
    return student


def _require_class(db: Session, class_id: int) -> SchoolClass:
    school_class = db.get(SchoolClass, class_id)
    if school_class is None:
        raise ClassNotFound
    return school_class


def _require_subject_in_class(db: Session, class_id: int, subject_id: int) -> Subject:
    subject = db.scalar(
        select(Subject).where(Subject.id == subject_id, Subject.class_id == class_id)
    )
    if subject is None:
        raise SubjectNotFound
    return subject


def _require_visible_class(db: Session, actor: User, class_id: int) -> SchoolClass:
    school_class = _require_class(db, class_id)
    if actor.role == UserRole.ADMIN:
        return school_class
    if actor.role != UserRole.TEACHER:
        raise AttendanceNotAllowed
    teacher = get_teacher_for_user(db, actor.id)
    if teacher is None:
        raise TeacherProfileNotFound
    if school_class.class_teacher_id == teacher.id:
        return school_class
    subject_id = db.scalar(
        select(Subject.id).where(
            Subject.class_id == class_id,
            Subject.teacher_id == teacher.id,
        )
    )
    if subject_id is None:
        raise ClassNotFound
    return school_class


def _require_visible_student(db: Session, actor: User, student_id: int) -> Student:
    student = db.scalar(
        select(Student).where(Student.id == student_id).options(joinedload(Student.user))
    )
    if student is None:
        raise StudentNotFound
    if actor.role == UserRole.ADMIN:
        return student
    if actor.role == UserRole.STUDENT:
        if student.user_id != actor.id:
            raise StudentNotFound
        return student
    if actor.role != UserRole.TEACHER:
        raise AttendanceNotAllowed
    teacher = get_teacher_for_user(db, actor.id)
    if teacher is None or not _teacher_sees_student(db, teacher.id, student.id):
        raise StudentNotFound
    return student


def _require_student_self(db: Session, actor: User, student_id: int) -> Student:
    """Return this student's own profile. Any other caller is not found."""
    if actor.role != UserRole.STUDENT:
        raise StudentNotFound
    student = _require_visible_student(db, actor, student_id)
    if student.user_id != actor.id:
        raise StudentNotFound
    return student


def _zero_counts() -> dict[AttendanceStatus, int]:
    return {status: 0 for status in AttendanceStatus}


def _status_counts(
    db: Session,
    *,
    student_id: int,
    subject_id: int | None,
    date_from: date | None,
    date_to: date | None,
) -> dict[AttendanceStatus, int]:
    rows = db.execute(
        _filtered(
            select(Attendance.status, func.count(Attendance.id)),
            restrict=None,
            class_id=None,
            subject_id=subject_id,
            student_id=student_id,
            day=None,
            date_from=date_from,
            date_to=date_to,
        ).group_by(Attendance.status)
    ).all()
    counts = _zero_counts()
    for status, count in rows:
        key = status if isinstance(status, AttendanceStatus) else AttendanceStatus(status)
        counts[key] = int(count)
    return counts


def _grouped_counts(
    db: Session,
    *,
    student_id: int,
    subject_id: int | None,
    date_from: date | None,
    date_to: date | None,
    column,
) -> dict[object, dict[AttendanceStatus, int]]:
    rows = db.execute(
        _filtered(
            select(column, Attendance.status, func.count(Attendance.id)),
            restrict=None,
            class_id=None,
            subject_id=subject_id,
            student_id=student_id,
            day=None,
            date_from=date_from,
            date_to=date_to,
        ).group_by(column, Attendance.status)
    ).all()
    grouped: dict[object, dict[AttendanceStatus, int]] = {}
    for key, status, count in rows:
        bucket = grouped.setdefault(key, _zero_counts())
        kind = status if isinstance(status, AttendanceStatus) else AttendanceStatus(status)
        bucket[kind] = int(count)
    return grouped


def _subjects_for_student(
    db: Session,
    student_id: int,
    marked_ids: set[object],
) -> list[Subject]:
    subjects: dict[int, Subject] = {}
    school_class = enrolled_class(db, student_id)
    if school_class is not None:
        current = db.scalars(
            select(Subject).where(
                Subject.class_id == school_class.id,
                Subject.is_active.is_(True),
            )
        ).all()
        for subject in current:
            subjects[subject.id] = subject
    missing = [int(subject_id) for subject_id in marked_ids if int(subject_id) not in subjects]
    if missing:
        earlier = db.scalars(select(Subject).where(Subject.id.in_(missing))).all()
        for subject in earlier:
            subjects[subject.id] = subject
    return sorted(subjects.values(), key=lambda subject: (subject.code, subject.name, subject.id))


def _month_span(year: int, month: int) -> tuple[date, date]:
    last = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _teacher_sees_student(db: Session, teacher_id: int, student_id: int) -> bool:
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


def _prepare_staff_read(
    db: Session,
    actor: User,
    class_id: int | None,
    subject_id: int | None,
) -> MarkRestrict:
    """Return a teacher scope, or None when an admin may read every class.

    A class teacher can read every subject in that class. A subject teacher
    can read only the subjects assigned to them.
    """
    if actor.role == UserRole.ADMIN:
        if class_id is not None:
            _require_class(db, class_id)
        return None
    if actor.role != UserRole.TEACHER:
        raise AttendanceNotAllowed
    restrict = _teacher_restrict(db, actor)
    if class_id is not None:
        _require_visible_class(db, actor, class_id)
    if subject_id is not None:
        _require_readable_subject(db, actor, subject_id, class_id)
    return restrict


def _teacher_restrict(db: Session, actor: User) -> tuple[set[int], set[int]]:
    """Return homeroom class ids and subject ids this teacher may read."""
    if actor.role != UserRole.TEACHER:
        raise AttendanceNotAllowed
    teacher = get_teacher_for_user(db, actor.id)
    if teacher is None:
        raise TeacherProfileNotFound
    homerooms = set(
        db.scalars(
            select(SchoolClass.id).where(SchoolClass.class_teacher_id == teacher.id)
        ).all()
    )
    subjects = set(
        db.scalars(select(Subject.id).where(Subject.teacher_id == teacher.id)).all()
    )
    return homerooms, subjects


def _require_readable_subject(
    db: Session,
    actor: User,
    subject_id: int,
    class_id: int | None,
) -> Subject:
    """Reject a subject this teacher does not teach and does not homeroom."""
    subject = db.get(Subject, subject_id)
    if subject is None or (class_id is not None and subject.class_id != class_id):
        raise SubjectNotFound
    if actor.role != UserRole.TEACHER:
        return subject
    teacher = get_teacher_for_user(db, actor.id)
    if teacher is None:
        raise TeacherProfileNotFound
    school_class = db.get(SchoolClass, subject.class_id)
    if school_class is None:
        raise SubjectNotFound
    if school_class.class_teacher_id == teacher.id or subject.teacher_id == teacher.id:
        return subject
    raise SubjectNotFound


def _require_session_teacher(db: Session, actor: User) -> Teacher:
    if actor.role != UserRole.TEACHER:
        raise AttendanceNotAllowed
    teacher = get_teacher_for_user(db, actor.id)
    if teacher is None:
        raise TeacherProfileNotFound
    return teacher


def _require_date_order(date_from: date | None, date_to: date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidDateRange


def _restrict_clause(restrict: MarkRestrict):
    if restrict is None:
        return None
    homerooms, subjects = restrict
    parts = []
    if homerooms:
        parts.append(Attendance.class_id.in_(homerooms))
    if subjects:
        parts.append(Attendance.subject_id.in_(subjects))
    if not parts:
        return false()
    if len(parts) == 1:
        return parts[0]
    return or_(*parts)


def _list_marks(
    db: Session,
    *,
    restrict: MarkRestrict,
    class_id: int | None,
    subject_id: int | None,
    student_id: int | None,
    day: date | None,
    date_from: date | None,
    date_to: date | None,
    page: int,
    page_size: int,
    newest_first: bool,
) -> tuple[list[Attendance], int]:
    filtered = _filtered(
        select(Attendance),
        restrict=restrict,
        class_id=class_id,
        subject_id=subject_id,
        student_id=student_id,
        day=day,
        date_from=date_from,
        date_to=date_to,
    )
    total = db.scalar(
        _filtered(
            select(func.count(Attendance.id)),
            restrict=restrict,
            class_id=class_id,
            subject_id=subject_id,
            student_id=student_id,
            day=day,
            date_from=date_from,
            date_to=date_to,
        )
    )
    if newest_first:
        ordered = filtered.order_by(Attendance.attendance_date.desc(), Attendance.id.desc())
    else:
        ordered = (
            filtered.join(Student, Attendance.student_id == Student.id)
            .order_by(Student.roll_number.asc(), Attendance.id.asc())
        )
    rows = db.scalars(
        ordered.options(joinedload(Attendance.student).joinedload(Student.user))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique().all()
    return list(rows), int(total or 0)


def _filtered(
    statement,
    *,
    restrict: MarkRestrict,
    class_id: int | None,
    subject_id: int | None,
    student_id: int | None,
    day: date | None,
    date_from: date | None,
    date_to: date | None,
):
    clause = _restrict_clause(restrict)
    if clause is not None:
        statement = statement.where(clause)
    if class_id is not None:
        statement = statement.where(Attendance.class_id == class_id)
    if subject_id is not None:
        statement = statement.where(Attendance.subject_id == subject_id)
    if student_id is not None:
        statement = statement.where(Attendance.student_id == student_id)
    if day is not None:
        statement = statement.where(Attendance.attendance_date == day)
    if date_from is not None:
        statement = statement.where(Attendance.attendance_date >= date_from)
    if date_to is not None:
        statement = statement.where(Attendance.attendance_date <= date_to)
    return statement


def _find_session(
    db: Session,
    *,
    teacher_id: int,
    class_id: int,
    subject_id: int,
    day: date,
) -> AttendanceSession | None:
    return db.scalar(
        select(AttendanceSession).where(
            AttendanceSession.teacher_id == teacher_id,
            AttendanceSession.class_id == class_id,
            AttendanceSession.subject_id == subject_id,
            AttendanceSession.session_date == day,
        )
    )


def _find_mark(db: Session, *, student_id: int, subject_id: int, day: date) -> Attendance | None:
    return db.scalar(
        select(Attendance)
        .where(
            Attendance.student_id == student_id,
            Attendance.subject_id == subject_id,
            Attendance.attendance_date == day,
        )
        .options(joinedload(Attendance.student).joinedload(Student.user))
    )


def _load_mark(db: Session, attendance_id: int) -> Attendance:
    record = db.scalar(
        select(Attendance)
        .where(Attendance.id == attendance_id)
        .options(joinedload(Attendance.student).joinedload(Student.user))
    )
    if record is None:
        raise StudentNotFound
    return record


def _status_for_check_in(check_in: datetime) -> AttendanceStatus:
    cutoff = get_settings().attendance_late_time
    if cutoff is None:
        return AttendanceStatus.PRESENT
    if check_in.time() >= cutoff:
        return AttendanceStatus.LATE
    return AttendanceStatus.PRESENT


def _confidence_score(confidence: float | None) -> Decimal | None:
    if confidence is None:
        return None
    score = Decimal(str(confidence)).quantize(Decimal("0.0001"))
    if score < Decimal("0") or score > Decimal("1"):
        raise ValueError("confidence must be between 0 and 1")
    return score


def _is_duplicate_session(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return "uq_attendance_sessions_lesson_date" in message or (
        "attendance_sessions.teacher_id" in message and "attendance_sessions.subject_id" in message
    )


def _is_duplicate_mark(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return "uq_attendance_student_subject_date" in message or (
        "attendance_records.student_id" in message and "attendance_records.subject_id" in message
    )


def _audit_session(
    db: Session,
    *,
    actor_id: int,
    session_id: int,
    class_id: int,
    subject_id: int,
    day: date,
    action: str,
    ip_address: str | None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type="attendance_session",
            entity_id=str(session_id),
            details={
                "class_id": class_id,
                "subject_id": subject_id,
                "date": day.isoformat(),
            },
            ip_address=ip_address,
        )
    )


def _audit(
    db: Session,
    *,
    actor_id: int,
    attendance_id: int,
    student_id: int,
    class_id: int,
    subject_id: int,
    status: AttendanceStatus,
    day: date,
    method: AttendanceMethod,
    ip_address: str | None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action="attendance.marked",
            entity_type="attendance",
            entity_id=str(attendance_id),
            details={
                "student_id": student_id,
                "class_id": class_id,
                "subject_id": subject_id,
                "status": status.value,
                "date": day.isoformat(),
                "recognition_method": method.value,
            },
            ip_address=ip_address,
        )
    )
