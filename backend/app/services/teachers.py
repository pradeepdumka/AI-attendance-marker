"""Create and maintain teacher profiles.

Creating a teacher inserts a `TEACHER` user and one teacher profile in
the same transaction. The plain password is hashed before insert and is
not written to the audit log. Deactivating a teacher keeps the profile
so later class assignment can still refer to it. This module does not
assign classes or subjects.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.auth.passwords import hash_password
from app.models.audit_log import AuditLog
from app.models.base import utcnow
from app.models.teacher import Teacher
from app.models.user import User, UserRole

_UPDATABLE = frozenset(
    {
        "first_name",
        "last_name",
        "email",
        "phone",
        "password",
        "employee_id",
        "department",
        "is_active",
    }
)
_SELF_UPDATABLE = frozenset({"first_name", "last_name", "phone", "department"})


class TeacherServiceError(Exception):
    """Expected staff-profile failure. The router chooses the HTTP response."""


class TeacherNotFound(TeacherServiceError):
    """No teacher profile has this id."""


class EmailAlreadyUsed(TeacherServiceError):
    """A user row already uses this email address."""


class DuplicateEmployeeId(TeacherServiceError):
    """Another teacher already uses this employee id."""


class PasswordMatchesEmail(TeacherServiceError):
    """The new password is the email address."""


class TeacherFieldNotAllowed(TeacherServiceError):
    """A teacher tried to change a field reserved for an admin."""


@dataclass(frozen=True, slots=True)
class NewTeacher:
    """Fields required to open a teacher account and profile."""

    first_name: str
    last_name: str
    email: str
    password: str
    phone: str | None
    employee_id: str
    department: str | None
    is_active: bool


def create_teacher(
    db: Session,
    *,
    actor_id: int,
    teacher: NewTeacher,
    ip_address: str | None = None,
) -> Teacher:
    """Insert a teacher user, profile, and audit row.

    A duplicate email or employee id raises before the insert when this
    transaction can already see the row, and again from the unique
    constraint when two requests race.
    """
    email = teacher.email.strip().lower()
    if teacher.password.casefold() == email.casefold():
        raise PasswordMatchesEmail
    if _email_taken(db, email):
        raise EmailAlreadyUsed
    if _employee_id_taken(db, teacher.employee_id):
        raise DuplicateEmployeeId

    user = User(
        first_name=teacher.first_name,
        last_name=teacher.last_name,
        email=email,
        password_hash=hash_password(teacher.password),
        phone=teacher.phone,
        role=UserRole.TEACHER,
        is_active=teacher.is_active,
    )
    db.add(user)
    try:
        db.flush()
        profile = Teacher(
            user_id=user.id,
            employee_code=teacher.employee_id,
            department=teacher.department,
        )
        db.add(profile)
        db.flush()
        _audit(
            db,
            actor_id=actor_id,
            action="teacher.created",
            teacher_id=profile.id,
            details={"employee_id": profile.employee_code},
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
    return _require_teacher(db, profile.id)


def get_teacher(db: Session, teacher_id: int) -> Teacher:
    """Return one teacher profile with the user loaded."""
    return _require_teacher(db, teacher_id)


def get_teacher_for_user(db: Session, user_id: int) -> Teacher | None:
    """Return the profile linked to this user, or `None` when there is none."""
    teacher_id = db.scalar(select(Teacher.id).where(Teacher.user_id == user_id))
    if teacher_id is None:
        return None
    return _load_teacher(db, teacher_id)


def list_teachers(
    db: Session,
    *,
    search: str | None,
    is_active: bool | None,
    department: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Teacher], int]:
    """Return one page of teachers and the total for the same filters.

    Search matches first name, last name, full name, email, phone,
    employee id, and department. Inactive teachers stay in the list
    unless `is_active` is set.
    """
    term = _clean_search(search)
    department_filter = _clean_search(department)
    filtered = _filtered(
        select(Teacher).join(Teacher.user),
        search=term,
        is_active=is_active,
        department=department_filter,
    )
    total = db.scalar(
        _filtered(
            select(func.count(Teacher.id)).select_from(Teacher).join(Teacher.user),
            search=term,
            is_active=is_active,
            department=department_filter,
        )
    )
    rows = db.scalars(
        filtered.options(contains_eager(Teacher.user))
        .order_by(User.last_name.asc(), User.first_name.asc(), Teacher.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique().all()
    return list(rows), int(total or 0)


def update_teacher(
    db: Session,
    *,
    actor_id: int,
    teacher_id: int,
    changes: Mapping[str, object],
    self_service: bool = False,
    ip_address: str | None = None,
) -> Teacher:
    """Apply a partial update after checks, then audit the changed fields.

    Checks run before any column changes. An empty change set returns
    the current row and does not write an audit record. When
    `self_service` is true, only name, phone, and department are accepted.
    """
    teacher = _require_teacher(db, teacher_id)
    if not changes:
        return teacher
    permitted = _SELF_UPDATABLE if self_service else _UPDATABLE
    if set(changes) - permitted:
        raise TeacherFieldNotAllowed
    _validate_update(db, teacher, changes)
    _apply_update(teacher, changes)
    teacher.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="teacher.updated",
        teacher_id=teacher.id,
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
    return _require_teacher(db, teacher.id)


def deactivate_teacher(
    db: Session,
    *,
    actor_id: int,
    teacher_id: int,
    ip_address: str | None = None,
) -> Teacher:
    """Mark the teacher account inactive. The profile row is kept.

    A second deactivate of the same teacher does not write another audit
    row. The teacher can sign in again only after an update sets the
    status back to active.
    """
    teacher = _require_teacher(db, teacher_id)
    if not teacher.user.is_active:
        return teacher
    teacher.user.is_active = False
    teacher.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="teacher.deactivated",
        teacher_id=teacher.id,
        details={"status": "INACTIVE"},
        ip_address=ip_address,
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _require_teacher(db, teacher.id)


def _validate_update(db: Session, teacher: Teacher, changes: Mapping[str, object]) -> None:
    email = teacher.user.email
    if "email" in changes:
        email = str(changes["email"]).strip().lower()
        if _email_taken(db, email, except_user_id=teacher.user_id):
            raise EmailAlreadyUsed
    if "password" in changes and str(changes["password"]).casefold() == email.casefold():
        raise PasswordMatchesEmail
    if "employee_id" in changes and _employee_id_taken(
        db,
        str(changes["employee_id"]),
        except_teacher_id=teacher.id,
    ):
        raise DuplicateEmployeeId


def _apply_update(teacher: Teacher, changes: Mapping[str, object]) -> None:
    user = teacher.user
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
    if "employee_id" in changes:
        teacher.employee_code = str(changes["employee_id"])
    if "department" in changes:
        department = changes["department"]
        teacher.department = None if department is None else str(department)


def _require_teacher(db: Session, teacher_id: int) -> Teacher:
    teacher = _load_teacher(db, teacher_id)
    if teacher is None:
        raise TeacherNotFound
    return teacher


def _load_teacher(db: Session, teacher_id: int) -> Teacher | None:
    return db.scalar(
        select(Teacher)
        .where(Teacher.id == teacher_id)
        .options(joinedload(Teacher.user))
    )


def _email_taken(db: Session, email: str, *, except_user_id: int | None = None) -> bool:
    statement = select(User.id).where(func.lower(User.email) == email.strip().lower())
    if except_user_id is not None:
        statement = statement.where(User.id != except_user_id)
    return db.scalar(statement) is not None


def _employee_id_taken(
    db: Session,
    employee_id: str,
    *,
    except_teacher_id: int | None = None,
) -> bool:
    statement = select(Teacher.id).where(
        func.lower(Teacher.employee_code) == employee_id.strip().lower()
    )
    if except_teacher_id is not None:
        statement = statement.where(Teacher.id != except_teacher_id)
    return db.scalar(statement) is not None


def _filtered(statement, *, search, is_active, department):
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
                Teacher.employee_code.ilike(pattern, escape="\\"),
                Teacher.department.ilike(pattern, escape="\\"),
            )
        )
    if is_active is not None:
        statement = statement.where(User.is_active.is_(is_active))
    if department is not None:
        statement = statement.where(func.lower(Teacher.department) == department.lower())
    return statement


def _clean_search(value: str | None) -> str | None:
    if value is None:
        return None
    term = " ".join(value.split())
    return term or None


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _audit(
    db: Session,
    *,
    actor_id: int,
    action: str,
    teacher_id: int,
    details: dict[str, object],
    ip_address: str | None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type="teacher",
            entity_id=str(teacher_id),
            details=details,
            ip_address=ip_address,
        )
    )


def _duplicate_error(exc: IntegrityError) -> TeacherServiceError | None:
    message = str(exc.orig).lower()
    if "uq_teachers_employee_code" in message or "teachers.employee_code" in message:
        return DuplicateEmployeeId()
    if "ix_users_email" in message or "users.email" in message:
        return EmailAlreadyUsed()
    return None
