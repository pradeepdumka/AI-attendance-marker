"""Create classes and subjects, assign teachers, and enroll students.

A class has one optional class teacher. A subject has one optional
teacher. An enrollment row is unique for a student and a class, and a
student has at most one active enrollment. Assigning someone who is
already in that slot does not insert another row. This module does not
mark attendance.
"""

from collections.abc import Mapping

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.audit_log import AuditLog
from app.models.base import utcnow
from app.models.class_model import SchoolClass
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.student import Student
from app.models.subject import Subject
from app.models.teacher import Teacher


class AcademicServiceError(Exception):
    """Expected class or subject failure. The router chooses the HTTP response."""


class ClassNotFound(AcademicServiceError):
    """No class has this id, or it is not assigned to this teacher."""


class SubjectNotFound(AcademicServiceError):
    """No subject has this id, or it is not assigned to this teacher."""


class TeacherNotFound(AcademicServiceError):
    """No teacher profile has this id."""


class StudentNotFound(AcademicServiceError):
    """No student profile has this id."""


class ClassNotActive(AcademicServiceError):
    """The class exists but is not open."""


class SubjectNotActive(AcademicServiceError):
    """The subject exists but is not open."""


class TeacherNotActive(AcademicServiceError):
    """The teacher account cannot be assigned."""


class StudentNotActive(AcademicServiceError):
    """The student account cannot be enrolled."""


class DuplicateClass(AcademicServiceError):
    """This name, section, and academic year are already used."""


class DuplicateSubjectCode(AcademicServiceError):
    """This subject code is already used in the class."""


class DuplicateEnrollment(AcademicServiceError):
    """The student already has an active enrollment in this class."""


class StudentEnrolledElsewhere(AcademicServiceError):
    """The student already has an active enrollment in a different class."""


class EnrollmentNotFound(AcademicServiceError):
    """The student has no active enrollment to move or remove."""


def create_class(
    db: Session,
    *,
    actor_id: int,
    name: str,
    section: str,
    academic_year: str,
    class_teacher_id: int | None,
    is_active: bool,
    ip_address: str | None = None,
) -> SchoolClass:
    """Insert one class. The name, section, and year must be unique together."""
    if _class_identity_taken(db, name, section, academic_year):
        raise DuplicateClass
    if class_teacher_id is not None:
        _require_active_teacher(db, class_teacher_id)
    school_class = SchoolClass(
        name=name,
        section=section,
        academic_year=academic_year,
        class_teacher_id=class_teacher_id,
        is_active=is_active,
    )
    db.add(school_class)
    try:
        db.flush()
        _audit(
            db,
            actor_id=actor_id,
            action="class.created",
            entity_type="class",
            entity_id=school_class.id,
            details={
                "name": name,
                "section": section,
                "academic_year": academic_year,
                "class_teacher_id": class_teacher_id,
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
    return _require_class(db, school_class.id)


def get_class(db: Session, class_id: int) -> SchoolClass:
    """Return one class for an admin."""
    return _require_class(db, class_id)


def enrolled_class(db: Session, student_id: int) -> SchoolClass | None:
    """Return the class on this student's current enrollment.

    A student has at most one active enrollment. When more than one row
    is stored, the latest `enrolled_on` wins, then the highest id.
    """
    class_id = db.scalar(
        select(Enrollment.class_id)
        .where(
            Enrollment.student_id == student_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
        .order_by(Enrollment.enrolled_on.desc(), Enrollment.id.desc())
        .limit(1)
    )
    if class_id is None:
        return None
    return db.get(SchoolClass, class_id)


def get_class_for_teacher(db: Session, class_id: int, teacher_id: int) -> SchoolClass:
    """Return an active class assigned to this teacher."""
    school_class = _require_class(db, class_id)
    if not school_class.is_active or not _class_assigned_to(db, school_class.id, teacher_id):
        raise ClassNotFound
    return school_class


def list_classes(
    db: Session,
    *,
    search: str | None,
    is_active: bool | None,
    academic_year: str | None,
    page: int,
    page_size: int,
    teacher_id: int | None = None,
) -> tuple[list[SchoolClass], int]:
    """Return one page of classes.

    When `teacher_id` is set, only active classes assigned to that
    teacher are returned. Assignment means the teacher is the class
    teacher or teaches an active subject in the class.
    """
    filtered = _classes_filtered(
        select(SchoolClass),
        search=_clean(search),
        is_active=is_active,
        academic_year=_clean(academic_year),
        teacher_id=teacher_id,
    )
    total = db.scalar(
        _classes_filtered(
            select(func.count(SchoolClass.id)),
            search=_clean(search),
            is_active=is_active,
            academic_year=_clean(academic_year),
            teacher_id=teacher_id,
        )
    )
    rows = db.scalars(
        filtered.order_by(
            SchoolClass.name.asc(),
            SchoolClass.section.asc(),
            SchoolClass.academic_year.asc(),
            SchoolClass.id.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), int(total or 0)


def update_class(
    db: Session,
    *,
    actor_id: int,
    class_id: int,
    changes: Mapping[str, object],
    ip_address: str | None = None,
) -> SchoolClass:
    """Apply a partial class update after checks."""
    school_class = _require_class(db, class_id)
    if not changes:
        return school_class
    _validate_class_changes(db, school_class, changes)
    if "name" in changes:
        school_class.name = str(changes["name"])
    if "section" in changes:
        school_class.section = str(changes["section"])
    if "academic_year" in changes:
        school_class.academic_year = str(changes["academic_year"])
    if "is_active" in changes:
        school_class.is_active = bool(changes["is_active"])
    if "class_teacher_id" in changes:
        teacher_id = changes["class_teacher_id"]
        school_class.class_teacher_id = None if teacher_id is None else int(teacher_id)
    school_class.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="class.updated",
        entity_type="class",
        entity_id=school_class.id,
        details={"fields": sorted(str(field) for field in changes)},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_class(db, school_class.id)


def deactivate_class(
    db: Session,
    *,
    actor_id: int,
    class_id: int,
    ip_address: str | None = None,
) -> SchoolClass:
    """Mark the class inactive and keep the row."""
    school_class = _require_class(db, class_id)
    if not school_class.is_active:
        return school_class
    school_class.is_active = False
    school_class.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="class.deactivated",
        entity_type="class",
        entity_id=school_class.id,
        details={"status": "INACTIVE"},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_class(db, school_class.id)


def assign_class_teacher(
    db: Session,
    *,
    actor_id: int,
    class_id: int,
    teacher_id: int,
    ip_address: str | None = None,
) -> SchoolClass:
    """Set the single class teacher. The same teacher is not assigned twice."""
    school_class = _require_class(db, class_id)
    _require_active_teacher(db, teacher_id)
    if school_class.class_teacher_id == teacher_id:
        return school_class
    school_class.class_teacher_id = teacher_id
    school_class.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="class.teacher_assigned",
        entity_type="class",
        entity_id=school_class.id,
        details={"class_teacher_id": teacher_id},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_class(db, school_class.id)


def remove_class_teacher(
    db: Session,
    *,
    actor_id: int,
    class_id: int,
    ip_address: str | None = None,
) -> SchoolClass:
    """Clear the class teacher. Removing an empty assignment changes nothing."""
    school_class = _require_class(db, class_id)
    if school_class.class_teacher_id is None:
        return school_class
    school_class.class_teacher_id = None
    school_class.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="class.teacher_removed",
        entity_type="class",
        entity_id=school_class.id,
        details={"class_teacher_id": None},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_class(db, school_class.id)


def create_subject(
    db: Session,
    *,
    actor_id: int,
    class_id: int,
    name: str,
    code: str,
    teacher_id: int | None,
    is_active: bool,
    ip_address: str | None = None,
) -> Subject:
    """Insert one subject. The code must be unique inside the class."""
    _require_active_class(db, class_id)
    if teacher_id is not None:
        _require_active_teacher(db, teacher_id)
    if _subject_code_taken(db, class_id, code):
        raise DuplicateSubjectCode
    subject = Subject(
        class_id=class_id,
        name=name,
        code=code,
        teacher_id=teacher_id,
        is_active=is_active,
    )
    db.add(subject)
    try:
        db.flush()
        _audit(
            db,
            actor_id=actor_id,
            action="subject.created",
            entity_type="subject",
            entity_id=subject.id,
            details={"class_id": class_id, "code": code, "teacher_id": teacher_id},
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
    return _require_subject(db, subject.id)


def get_subject(db: Session, subject_id: int) -> Subject:
    """Return one subject for an admin."""
    return _require_subject(db, subject_id)


def get_subject_for_teacher(db: Session, subject_id: int, teacher_id: int) -> Subject:
    """Return an active subject assigned to this teacher in an active class."""
    subject = _require_subject(db, subject_id)
    school_class = _require_class(db, subject.class_id)
    if (
        subject.teacher_id != teacher_id
        or not subject.is_active
        or not school_class.is_active
    ):
        raise SubjectNotFound
    return subject


def list_subjects(
    db: Session,
    *,
    search: str | None,
    is_active: bool | None,
    class_id: int | None,
    teacher_id: int | None,
    page: int,
    page_size: int,
    assigned_teacher_id: int | None = None,
) -> tuple[list[Subject], int]:
    """Return one page of subjects.

    `assigned_teacher_id` limits the page to active subjects that teacher
    teaches in an active class. `teacher_id` is an admin filter.
    """
    filtered = _subjects_filtered(
        select(Subject).join(Subject.school_class),
        search=_clean(search),
        is_active=is_active,
        class_id=class_id,
        teacher_id=teacher_id,
        assigned_teacher_id=assigned_teacher_id,
    )
    total = db.scalar(
        _subjects_filtered(
            select(func.count(Subject.id)).select_from(Subject).join(Subject.school_class),
            search=_clean(search),
            is_active=is_active,
            class_id=class_id,
            teacher_id=teacher_id,
            assigned_teacher_id=assigned_teacher_id,
        )
    )
    rows = db.scalars(
        filtered.order_by(Subject.code.asc(), Subject.name.asc(), Subject.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), int(total or 0)


def update_subject(
    db: Session,
    *,
    actor_id: int,
    subject_id: int,
    changes: Mapping[str, object],
    ip_address: str | None = None,
) -> Subject:
    """Apply a partial subject update after checks."""
    subject = _require_subject(db, subject_id)
    if not changes:
        return subject
    _validate_subject_changes(db, subject, changes)
    if "class_id" in changes:
        subject.class_id = int(changes["class_id"])
    if "name" in changes:
        subject.name = str(changes["name"])
    if "code" in changes:
        subject.code = str(changes["code"])
    if "is_active" in changes:
        subject.is_active = bool(changes["is_active"])
    if "teacher_id" in changes:
        teacher_id = changes["teacher_id"]
        subject.teacher_id = None if teacher_id is None else int(teacher_id)
    subject.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="subject.updated",
        entity_type="subject",
        entity_id=subject.id,
        details={"fields": sorted(str(field) for field in changes)},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_subject(db, subject.id)


def deactivate_subject(
    db: Session,
    *,
    actor_id: int,
    subject_id: int,
    ip_address: str | None = None,
) -> Subject:
    """Mark the subject inactive and keep the row."""
    subject = _require_subject(db, subject_id)
    if not subject.is_active:
        return subject
    subject.is_active = False
    subject.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="subject.deactivated",
        entity_type="subject",
        entity_id=subject.id,
        details={"status": "INACTIVE"},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_subject(db, subject.id)


def assign_subject_teacher(
    db: Session,
    *,
    actor_id: int,
    subject_id: int,
    teacher_id: int,
    ip_address: str | None = None,
) -> Subject:
    """Set the single subject teacher. The same teacher is not assigned twice."""
    subject = _require_subject(db, subject_id)
    _require_active_teacher(db, teacher_id)
    if subject.teacher_id == teacher_id:
        return subject
    subject.teacher_id = teacher_id
    subject.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="subject.teacher_assigned",
        entity_type="subject",
        entity_id=subject.id,
        details={"teacher_id": teacher_id},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_subject(db, subject.id)


def remove_subject_teacher(
    db: Session,
    *,
    actor_id: int,
    subject_id: int,
    ip_address: str | None = None,
) -> Subject:
    """Clear the subject teacher. Removing an empty assignment changes nothing."""
    subject = _require_subject(db, subject_id)
    if subject.teacher_id is None:
        return subject
    subject.teacher_id = None
    subject.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="subject.teacher_removed",
        entity_type="subject",
        entity_id=subject.id,
        details={"teacher_id": None},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_subject(db, subject.id)


def enroll_student(
    db: Session,
    *,
    actor_id: int,
    class_id: int,
    student_id: int,
    ip_address: str | None = None,
) -> tuple[Enrollment, bool]:
    """Enroll a student in a class.

    An active enrollment in this class is a duplicate. An active
    enrollment in another class must be moved instead. A withdrawn row
    for this pair is reactivated, so the unique student and class pair
    is not inserted again. The boolean is true when a new row is inserted.
    """
    _require_active_class(db, class_id)
    _require_active_student(db, student_id)
    existing = _find_enrollment(db, student_id, class_id)
    if existing is not None and existing.status == EnrollmentStatus.ACTIVE:
        raise DuplicateEnrollment
    if _active_enrollment_elsewhere(db, student_id, class_id) is not None:
        raise StudentEnrolledElsewhere
    created = existing is None
    if existing is None:
        existing = Enrollment(
            student_id=student_id,
            class_id=class_id,
            status=EnrollmentStatus.ACTIVE,
            enrolled_on=utcnow().date(),
        )
        db.add(existing)
        _flush(db)
    else:
        existing.status = EnrollmentStatus.ACTIVE
        existing.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="enrollment.created" if created else "enrollment.reactivated",
        entity_type="enrollment",
        entity_id=existing.id,
        details={"student_id": student_id, "class_id": class_id},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_enrollment(db, existing.id), created


def move_student(
    db: Session,
    *,
    actor_id: int,
    class_id: int,
    student_id: int,
    ip_address: str | None = None,
) -> Enrollment:
    """Move a student's active enrollment to another class.

    Other active enrollments are withdrawn. The destination reuses an
    existing row for that student and class when one is already stored.
    """
    _require_active_class(db, class_id)
    _require_active_student(db, student_id)
    current = _active_enrollments(db, student_id)
    if not current:
        raise EnrollmentNotFound
    if any(enrollment.class_id == class_id for enrollment in current):
        raise DuplicateEnrollment
    for enrollment in current:
        enrollment.status = EnrollmentStatus.WITHDRAWN
        enrollment.updated_at = utcnow()
    destination = _find_enrollment(db, student_id, class_id)
    if destination is None:
        destination = Enrollment(
            student_id=student_id,
            class_id=class_id,
            status=EnrollmentStatus.ACTIVE,
            enrolled_on=utcnow().date(),
        )
        db.add(destination)
        _flush(db)
    else:
        destination.status = EnrollmentStatus.ACTIVE
        destination.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="enrollment.moved",
        entity_type="enrollment",
        entity_id=destination.id,
        details={"student_id": student_id, "class_id": class_id},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_enrollment(db, destination.id)


def remove_student(
    db: Session,
    *,
    actor_id: int,
    class_id: int,
    student_id: int,
    ip_address: str | None = None,
) -> Enrollment:
    """Withdraw an active enrollment. The row is kept."""
    _require_class(db, class_id)
    _require_student(db, student_id)
    enrollment = _find_enrollment(db, student_id, class_id)
    if enrollment is None or enrollment.status != EnrollmentStatus.ACTIVE:
        raise EnrollmentNotFound
    enrollment.status = EnrollmentStatus.WITHDRAWN
    enrollment.updated_at = utcnow()
    _audit(
        db,
        actor_id=actor_id,
        action="enrollment.removed",
        entity_type="enrollment",
        entity_id=enrollment.id,
        details={"student_id": student_id, "class_id": class_id},
        ip_address=ip_address,
    )
    _commit(db)
    return _require_enrollment(db, enrollment.id)


def list_class_students(
    db: Session,
    *,
    class_id: int,
    status: EnrollmentStatus | None,
    page: int,
    page_size: int,
) -> tuple[list[Enrollment], int]:
    """Return memberships for one class. The class must exist."""
    _require_class(db, class_id)
    filtered = _enrollments_filtered(select(Enrollment), class_id=class_id, status=status)
    total = db.scalar(
        _enrollments_filtered(
            select(func.count(Enrollment.id)),
            class_id=class_id,
            status=status,
        )
    )
    rows = db.scalars(
        filtered.options(
            joinedload(Enrollment.student).joinedload(Student.user),
        )
        .order_by(Enrollment.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique().all()
    return list(rows), int(total or 0)


def _validate_class_changes(
    db: Session,
    school_class: SchoolClass,
    changes: Mapping[str, object],
) -> None:
    name = str(changes["name"]) if "name" in changes else school_class.name
    section = str(changes["section"]) if "section" in changes else school_class.section
    academic_year = (
        str(changes["academic_year"]) if "academic_year" in changes else school_class.academic_year
    )
    if _class_identity_taken(
        db,
        name,
        section,
        academic_year,
        except_class_id=school_class.id,
    ):
        raise DuplicateClass
    if "class_teacher_id" in changes and changes["class_teacher_id"] is not None:
        _require_active_teacher(db, int(changes["class_teacher_id"]))


def _validate_subject_changes(db: Session, subject: Subject, changes: Mapping[str, object]) -> None:
    class_id = int(changes["class_id"]) if "class_id" in changes else subject.class_id
    if "class_id" in changes:
        _require_active_class(db, class_id)
    code = str(changes["code"]) if "code" in changes else subject.code
    if _subject_code_taken(db, class_id, code, except_subject_id=subject.id):
        raise DuplicateSubjectCode
    if "teacher_id" in changes and changes["teacher_id"] is not None:
        _require_active_teacher(db, int(changes["teacher_id"]))


def _classes_filtered(statement, *, search, is_active, academic_year, teacher_id):
    if teacher_id is not None:
        assigned_subject = (
            select(Subject.id)
            .where(
                Subject.class_id == SchoolClass.id,
                Subject.teacher_id == teacher_id,
                Subject.is_active.is_(True),
            )
            .exists()
        )
        statement = statement.where(
            SchoolClass.is_active.is_(True),
            or_(SchoolClass.class_teacher_id == teacher_id, assigned_subject),
        )
    if search is not None:
        pattern = _like_pattern(search)
        label = SchoolClass.name + " " + SchoolClass.section
        statement = statement.where(
            or_(
                SchoolClass.name.ilike(pattern, escape="\\"),
                SchoolClass.section.ilike(pattern, escape="\\"),
                SchoolClass.academic_year.ilike(pattern, escape="\\"),
                label.ilike(pattern, escape="\\"),
            )
        )
    if is_active is not None:
        statement = statement.where(SchoolClass.is_active.is_(is_active))
    if academic_year is not None:
        statement = statement.where(func.lower(SchoolClass.academic_year) == academic_year.lower())
    return statement


def _subjects_filtered(
    statement,
    *,
    search,
    is_active,
    class_id,
    teacher_id,
    assigned_teacher_id,
):
    if assigned_teacher_id is not None:
        statement = statement.where(
            Subject.teacher_id == assigned_teacher_id,
            Subject.is_active.is_(True),
            SchoolClass.is_active.is_(True),
        )
    if search is not None:
        pattern = _like_pattern(search)
        statement = statement.where(
            or_(
                Subject.name.ilike(pattern, escape="\\"),
                Subject.code.ilike(pattern, escape="\\"),
                SchoolClass.name.ilike(pattern, escape="\\"),
            )
        )
    if is_active is not None:
        statement = statement.where(Subject.is_active.is_(is_active))
    if class_id is not None:
        statement = statement.where(Subject.class_id == class_id)
    if teacher_id is not None:
        statement = statement.where(Subject.teacher_id == teacher_id)
    return statement


def _enrollments_filtered(statement, *, class_id, status):
    statement = statement.where(Enrollment.class_id == class_id)
    if status is not None:
        statement = statement.where(Enrollment.status == status)
    return statement


def _class_assigned_to(db: Session, class_id: int, teacher_id: int) -> bool:
    school_class = db.get(SchoolClass, class_id)
    if school_class is not None and school_class.class_teacher_id == teacher_id:
        return True
    subject_id = db.scalar(
        select(Subject.id).where(
            Subject.class_id == class_id,
            Subject.teacher_id == teacher_id,
            Subject.is_active.is_(True),
        )
    )
    return subject_id is not None


def _require_class(db: Session, class_id: int) -> SchoolClass:
    school_class = db.get(SchoolClass, class_id)
    if school_class is None:
        raise ClassNotFound
    return school_class


def _require_active_class(db: Session, class_id: int) -> SchoolClass:
    school_class = _require_class(db, class_id)
    if not school_class.is_active:
        raise ClassNotActive
    return school_class


def _require_subject(db: Session, subject_id: int) -> Subject:
    subject = db.get(Subject, subject_id)
    if subject is None:
        raise SubjectNotFound
    return subject


def _require_active_teacher(db: Session, teacher_id: int) -> Teacher:
    teacher = db.scalar(
        select(Teacher)
        .where(Teacher.id == teacher_id)
        .options(joinedload(Teacher.user))
    )
    if teacher is None:
        raise TeacherNotFound
    if not teacher.user.is_active:
        raise TeacherNotActive
    return teacher


def _require_student(db: Session, student_id: int) -> Student:
    student = db.get(Student, student_id)
    if student is None:
        raise StudentNotFound
    return student


def _require_active_student(db: Session, student_id: int) -> Student:
    student = db.scalar(
        select(Student)
        .where(Student.id == student_id)
        .options(joinedload(Student.user))
    )
    if student is None:
        raise StudentNotFound
    if not student.user.is_active:
        raise StudentNotActive
    return student


def _require_enrollment(db: Session, enrollment_id: int) -> Enrollment:
    enrollment = db.scalar(
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(joinedload(Enrollment.student).joinedload(Student.user))
    )
    if enrollment is None:
        raise EnrollmentNotFound
    return enrollment


def _find_enrollment(db: Session, student_id: int, class_id: int) -> Enrollment | None:
    return db.scalar(
        select(Enrollment).where(
            Enrollment.student_id == student_id,
            Enrollment.class_id == class_id,
        )
    )


def _active_enrollments(db: Session, student_id: int) -> list[Enrollment]:
    return list(
        db.scalars(
            select(Enrollment).where(
                Enrollment.student_id == student_id,
                Enrollment.status == EnrollmentStatus.ACTIVE,
            )
        ).all()
    )


def _active_enrollment_elsewhere(
    db: Session,
    student_id: int,
    class_id: int,
) -> Enrollment | None:
    return db.scalar(
        select(Enrollment).where(
            Enrollment.student_id == student_id,
            Enrollment.class_id != class_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
    )


def _class_identity_taken(
    db: Session,
    name: str,
    section: str,
    academic_year: str,
    *,
    except_class_id: int | None = None,
) -> bool:
    statement = select(SchoolClass.id).where(
        func.lower(SchoolClass.name) == name.strip().lower(),
        func.lower(SchoolClass.section) == section.strip().lower(),
        func.lower(SchoolClass.academic_year) == academic_year.strip().lower(),
    )
    if except_class_id is not None:
        statement = statement.where(SchoolClass.id != except_class_id)
    return db.scalar(statement) is not None


def _subject_code_taken(
    db: Session,
    class_id: int,
    code: str,
    *,
    except_subject_id: int | None = None,
) -> bool:
    statement = select(Subject.id).where(
        Subject.class_id == class_id,
        func.lower(Subject.code) == code.strip().lower(),
    )
    if except_subject_id is not None:
        statement = statement.where(Subject.id != except_subject_id)
    return db.scalar(statement) is not None


def _clean(value: str | None) -> str | None:
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
    entity_type: str,
    entity_id: int,
    details: dict[str, object],
    ip_address: str | None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details=details,
            ip_address=ip_address,
        )
    )


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        duplicate = _duplicate_error(exc)
        if duplicate is not None:
            raise duplicate from None
        raise
    except Exception:
        db.rollback()
        raise


def _commit(db: Session) -> None:
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


def _duplicate_error(exc: IntegrityError) -> AcademicServiceError | None:
    message = str(exc.orig).lower()
    if "uq_classes_name_section_year" in message or (
        "classes.name" in message and "classes.section" in message
    ):
        return DuplicateClass()
    if "uq_subjects_class_id_code" in message or (
        "subjects.class_id" in message and "subjects.code" in message
    ):
        return DuplicateSubjectCode()
    if "uq_enrollments_student_id_class_id" in message or (
        "enrollments.student_id" in message and "enrollments.class_id" in message
    ):
        return DuplicateEnrollment()
    return None
