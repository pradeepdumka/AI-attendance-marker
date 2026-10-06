"""Create and maintain student roster rows.

Creating a student inserts a `STUDENT` user and one student profile in
the same transaction. The plain password is hashed before insert and is
not written to the audit log. Deleting a student deactivates the user
account. The roster row stays so enrollments and later attendance rows
remain valid. This module does not read or write face encodings.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager, selectinload

from app.auth.passwords import hash_password
from app.models.audit_log import AuditLog
from app.models.base import utcnow
from app.models.class_model import SchoolClass
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.student import Gender, Student
from app.models.user import User, UserRole

_UPDATABLE = frozenset(
    {
        "first_name",
        "last_name",
        "email",
        "phone",
        "password",
        "roll_number",
        "date_of_birth",
        "gender",
        "class_id",
        "is_active",
    }
)


class StudentServiceError(Exception):
    """Expected roster failure. The router chooses the HTTP response."""


class StudentNotFound(StudentServiceError):
    """No student profile has this id."""


class EmailAlreadyUsed(StudentServiceError):
    """A user row already uses this email address."""


class DuplicateRollNumber(StudentServiceError):
    """Another student already uses this roll number."""


class ClassNotFound(StudentServiceError):
    """No class has this id."""


class ClassNotActive(StudentServiceError):
    """The class exists but is not open for enrollment."""


class PasswordMatchesEmail(StudentServiceError):
    """The new password is the email address."""


@dataclass(frozen=True, slots=True)
class NewStudent:
    """Fields required to open a student account and roster row."""

    first_name: str
    last_name: str
    email: str
    password: str
    phone: str | None
    roll_number: str
    date_of_birth: date | None
    gender: Gender | None
    class_id: int | None
    is_active: bool


def create_student(
    db: Session,
    *,
    actor_id: int,
    student: NewStudent,
    ip_address: str | None = None,
) -> Student:
    """Insert a student user, profile, optional enrollment, and audit row.

    A duplicate email or roll number raises before the insert when this
    transaction can already see the row, and again from the unique
    constraint when two requests race.
    """
    email = student.email.strip().lower()
    if student.password.casefold() == email.casefold():
        raise PasswordMatchesEmail
    if _email_taken(db, email):
        raise EmailAlreadyUsed
    if _roll_taken(db, student.roll_number):
        raise DuplicateRollNumber
    school_class = None
    if student.class_id is not None:
        school_class = _require_assignable_class(db, student.class_id)

    user = User(
        first_name=student.first_name,
        last_name=student.last_name,
        email=email,
        password_hash=hash_password(student.password),
        phone=student.phone,
        role=UserRole.STUDENT,
        is_active=student.is_active,
    )
    db.add(user)
    try:
        db.flush()
        profile = Student(
            user_id=user.id,
            roll_number=student.roll_number,
            date_of_birth=student.date_of_birth,
            gender=student.gender,
        )
        db.add(profile)
        db.flush()
        if school_class is not None:
            _set_current_class(profile, school_class)
        _audit(
            db,
            actor_id=actor_id,
            action="student.created",
            student_id=profile.id,
            details={
                "roll_number": profile.roll_number,
                "class_id": None if school_class is None else school_class.id,
            },
            ip_address=ip_address,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        duplicate = _duplicate_error(exc)
        if duplicate is not None:
            raise duplicate from None
        raise
    except Exception:
        db.rollback()
        raise
    return _require_student(db, profile.id)


def get_student(db: Session, student_id: int) -> Student:
    """Return one student profile with the user and enrollments loaded."""
    return _require_student(db, student_id)


def get_student_for_user(db: Session, user_id: int) -> Student | None:
    """Return the profile linked to this user, or `None` when there is none."""
    student_id = db.scalar(select(Student.id).where(Student.user_id == user_id))
    if student_id is None:
        return None
    return _load_student(db, student_id)


def list_students(
    db: Session,
    *,
    search: str | None,
    is_active: bool | None,
    class_id: int | None,
    gender: Gender | None,
    page: int,
    page_size: int,
) -> tuple[list[Student], int]:
    """Return one page of students and the total for the same filters.

    Search matches first name, last name, full name, email, phone, and
    roll number. `class_id` keeps students with an active enrollment in
    that class. Inactive students stay in the list unless `is_active`
    is set.
    """
    term = _clean_search(search)
    filtered = _filtered(
        select(Student).join(Student.user),
        search=term,
        is_active=is_active,
        class_id=class_id,
        gender=gender,
    )
    total = db.scalar(
        _filtered(
            select(func.count(Student.id)).select_from(Student).join(Student.user),
            search=term,
            is_active=is_active,
            class_id=class_id,
            gender=gender,
        )
    )
    rows = db.scalars(
        filtered.options(
            contains_eager(Student.user),
            selectinload(Student.enrollments),
        )
        .order_by(User.last_name.asc(), User.first_name.asc(), Student.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique().all()
    return list(rows), int(total or 0)


def update_student(
    db: Session,
    *,
    actor_id: int,
    student_id: int,
    changes: Mapping[str, object],
    ip_address: str | None = None,
) -> Student:
    """Apply a partial update after checks, then audit the changed fields.

    Checks run before any column changes, so a missing class or a
    duplicate email leaves the stored student untouched. An empty change
    set returns the current row and does not write an audit record.
    """
    student = _require_student(db, student_id)
    if not changes:
        return student
    unknown = set(changes) - _UPDATABLE
    if unknown:
        raise StudentServiceError
    _validate_update(db, student, changes)
    _apply_update(student, changes)
    student.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="student.updated",
        student_id=student.id,
        details={"fields": sorted(str(field) for field in changes)},
        ip_address=ip_address,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        duplicate = _duplicate_error(exc)
        if duplicate is not None:
            raise duplicate from None
        raise
    except Exception:
        db.rollback()
        raise
    return _require_student(db, student.id)


def deactivate_student(
    db: Session,
    *,
    actor_id: int,
    student_id: int,
    ip_address: str | None = None,
) -> Student:
    """Mark the student account inactive. The profile row is kept.

    A second deactivate of the same student does not write another audit
    row. The student can sign in again only after an update sets the
    status back to active.
    """
    student = _require_student(db, student_id)
    if not student.user.is_active:
        return student
    student.user.is_active = False
    student.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="student.deactivated",
        student_id=student.id,
        details={"status": "INACTIVE"},
        ip_address=ip_address,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _require_student(db, student.id)


def _validate_update(db: Session, student: Student, changes: Mapping[str, object]) -> None:
    email = student.user.email
    if "email" in changes:
        email = str(changes["email"]).strip().lower()
        if _email_taken(db, email, except_user_id=student.user_id):
            raise EmailAlreadyUsed
    if "password" in changes and str(changes["password"]).casefold() == email.casefold():
        raise PasswordMatchesEmail
    if "roll_number" in changes and _roll_taken(
        db,
        str(changes["roll_number"]),
        except_student_id=student.id,
    ):
        raise DuplicateRollNumber
    if "class_id" in changes and changes["class_id"] is not None:
        _require_assignable_class(db, int(changes["class_id"]))


def _apply_update(student: Student, changes: Mapping[str, object]) -> None:
    user = student.user
    if "first_name" in changes:
        user.first_name = str(changes["first_name"])
    if "last_name" in changes:
        user.last_name = str(changes["last_name"])
    if "email" in changes:
        user.email = str(changes["email"]).strip().lower()
    if "phone" in changes:
        phone = changes["phone"]
        user.phone = None if phone is None else str(phone)
    if "password" in changes:
        user.password_hash = hash_password(str(changes["password"]))
    if "is_active" in changes:
        user.is_active = bool(changes["is_active"])
    if "roll_number" in changes:
        student.roll_number = str(changes["roll_number"])
    if "date_of_birth" in changes:
        dob = changes["date_of_birth"]
        if dob is not None and not isinstance(dob, date):
            raise StudentServiceError
        student.date_of_birth = dob
    if "gender" in changes:
        gender = changes["gender"]
        if gender is not None and not isinstance(gender, Gender):
            raise StudentServiceError
        student.gender = gender
    if "class_id" in changes:
        class_id = changes["class_id"]
        school_class = (
            None
            if class_id is None
            else _require_assignable_class(_session(student), int(class_id))
        )
        _set_current_class(student, school_class)


def _session(student: Student) -> Session:
    session = Session.object_session(student)
    if session is None:
        raise StudentServiceError
    return session


def _require_assignable_class(db: Session, class_id: int) -> SchoolClass:
    school_class = db.get(SchoolClass, class_id)
    if school_class is None:
        raise ClassNotFound
    if not school_class.is_active:
        raise ClassNotActive
    return school_class


def _set_current_class(student: Student, school_class: SchoolClass | None) -> None:
    """Leave the student in at most one active class.

    An existing enrollment for the target class is reactivated. Other
    active enrollments are marked withdrawn. Passing `None` withdraws
    every active enrollment and does not delete those rows.
    """
    target_id = None if school_class is None else school_class.id
    matched = False
    for enrollment in student.enrollments:
        if target_id is not None and enrollment.class_id == target_id:
            enrollment.status = EnrollmentStatus.ACTIVE
            matched = True
        elif enrollment.status == EnrollmentStatus.ACTIVE:
            enrollment.status = EnrollmentStatus.WITHDRAWN
    if target_id is not None and not matched:
        student.enrollments.append(
            Enrollment(
                class_id=target_id,
                status=EnrollmentStatus.ACTIVE,
                enrolled_on=utcnow().date(),
            )
        )


def _require_student(db: Session, student_id: int) -> Student:
    student = _load_student(db, student_id)
    if student is None:
        raise StudentNotFound
    return student


def _load_student(db: Session, student_id: int) -> Student | None:
    return db.scalar(
        select(Student)
        .where(Student.id == student_id)
        .options(
            selectinload(Student.user),
            selectinload(Student.enrollments),
        )
    )


def _email_taken(db: Session, email: str, *, except_user_id: int | None = None) -> bool:
    statement = select(User.id).where(func.lower(User.email) == email.strip().lower())
    if except_user_id is not None:
        statement = statement.where(User.id != except_user_id)
    return db.scalar(statement) is not None


def _roll_taken(db: Session, roll_number: str, *, except_student_id: int | None = None) -> bool:
    statement = select(Student.id).where(
        func.lower(Student.roll_number) == roll_number.strip().lower()
    )
    if except_student_id is not None:
        statement = statement.where(Student.id != except_student_id)
    return db.scalar(statement) is not None


def _filtered(statement, *, search, is_active, class_id, gender):
    if search is not None:
        pattern = _like_pattern(search)
        full_name = User.first_name + " " + User.last_name
        statement = statement.where(
            or_(
                User.first_name.ilike(pattern, escape="\\"),
                User.last_name.ilike(pattern, escape="\\"),
                full_name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
                User.phone.ilike(pattern, escape="\\"),
                Student.roll_number.ilike(pattern, escape="\\"),
            )
        )
    if is_active is not None:
        statement = statement.where(User.is_active.is_(is_active))
    if gender is not None:
        statement = statement.where(Student.gender == gender)
    if class_id is not None:
        statement = statement.where(
            Student.enrollments.any(
                (Enrollment.class_id == class_id)
                & (Enrollment.status == EnrollmentStatus.ACTIVE)
            )
        )
    return statement


def _clean_search(search: str | None) -> str | None:
    if search is None:
        return None
    term = " ".join(search.split())
    return term or None


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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


def _duplicate_error(exc: IntegrityError) -> StudentServiceError | None:
    message = str(exc.orig).lower()
    if "roll_number" in message or "uq_students_roll_number" in message:
        return DuplicateRollNumber()
    if "ix_users_email" in message or "users.email" in message:
        return EmailAlreadyUsed()
    return None
