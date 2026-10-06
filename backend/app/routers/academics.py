"""Admin and teacher routes for classes, subjects, and enrollment.

Admins manage the catalog and assignments. A teacher can read only the
active classes and subjects assigned to that teacher. Students cannot
call these routes.
"""

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import AdminUser, TeacherUser
from app.database.connection import get_db
from app.models.enrollment import EnrollmentStatus
from app.schemas.academics import (
    ClassCreateRequest,
    ClassListResponse,
    ClassResponse,
    ClassUpdateRequest,
    EnrollmentListResponse,
    EnrollmentResponse,
    EnrollStudentRequest,
    RecordStatus,
    SubjectCreateRequest,
    SubjectListResponse,
    SubjectResponse,
    SubjectUpdateRequest,
    TeacherAssignmentRequest,
    to_class_response,
    to_enrollment_response,
    to_subject_response,
)
from app.services.academics import (
    AcademicServiceError,
    ClassNotActive,
    ClassNotFound,
    DuplicateClass,
    DuplicateEnrollment,
    DuplicateSubjectCode,
    EnrollmentNotFound,
    StudentEnrolledElsewhere,
    StudentNotActive,
    StudentNotFound,
    SubjectNotActive,
    SubjectNotFound,
    TeacherNotActive,
    TeacherNotFound,
    assign_class_teacher,
    assign_subject_teacher,
    create_class,
    create_subject,
    deactivate_class,
    deactivate_subject,
    enroll_student,
    get_class,
    get_class_for_teacher,
    get_subject,
    get_subject_for_teacher,
    list_class_students,
    list_classes,
    list_subjects,
    move_student,
    remove_class_teacher,
    remove_student,
    remove_subject_teacher,
    update_class,
    update_subject,
)
from app.services.teachers import get_teacher_for_user

classes_router = APIRouter(prefix="/admin/classes", tags=["classes"])
subjects_router = APIRouter(prefix="/admin/subjects", tags=["subjects"])
assigned_router = APIRouter(prefix="/teacher", tags=["teacher"])

DbSession = Annotated[Session, Depends(get_db)]
ClassId = Annotated[int, Path(ge=1, description="Class id")]
SubjectId = Annotated[int, Path(ge=1, description="Subject id")]
StudentId = Annotated[int, Path(ge=1, description="Student profile id")]


def _admin_read_responses(not_found: str | None = None) -> dict[int, dict[str, str]]:
    responses = {
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
    }
    if not_found is not None:
        responses[404] = {"description": not_found}
    return responses


def _admin_write_responses(conflict: str) -> dict[int, dict[str, str]]:
    return {
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
        404: {"description": "Class, subject, teacher, or student not found"},
        409: {"description": conflict},
        422: {"description": "Invalid fields"},
    }


def _teacher_read_responses() -> dict[int, dict[str, str]]:
    return {
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as an admin or a student"},
        404: {"description": "No teacher profile, or the record is not assigned to this teacher"},
    }


@classes_router.post(
    "",
    response_model=ClassResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_admin_write_responses("Class identity already exists"),
)
def create_class_route(
    body: ClassCreateRequest,
    request: Request,
    response: Response,
    current_user: AdminUser,
    db: DbSession,
) -> ClassResponse:
    """Create a class section for one academic year."""
    try:
        school_class = create_class(
            db,
            actor_id=current_user.id,
            name=body.name,
            section=body.section,
            academic_year=body.academic_year,
            class_teacher_id=body.class_teacher_id,
            is_active=body.status == RecordStatus.ACTIVE,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    response.headers["Location"] = f"/admin/classes/{school_class.id}"
    return to_class_response(school_class)


@classes_router.get(
    "",
    response_model=ClassListResponse,
    responses=_admin_read_responses(),
)
def list_classes_route(
    _admin: AdminUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    account_status: Annotated[RecordStatus | None, Query(alias="status")] = None,
    academic_year: Annotated[str | None, Query(max_length=40)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ClassListResponse:
    """List classes. Search matches name, section, and academic year."""
    rows, total = list_classes(
        db,
        search=search,
        is_active=_is_active(account_status),
        academic_year=academic_year,
        page=page,
        page_size=page_size,
    )
    return ClassListResponse(
        items=[to_class_response(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@classes_router.get(
    "/{class_id}",
    response_model=ClassResponse,
    responses=_admin_read_responses("Class not found"),
)
def read_class(class_id: ClassId, _admin: AdminUser, db: DbSession) -> ClassResponse:
    """Return one class, including a deactivated section."""
    try:
        school_class = get_class(db, class_id)
    except AcademicServiceError as exc:
        _reject(exc)
    return to_class_response(school_class)


@classes_router.patch(
    "/{class_id}",
    response_model=ClassResponse,
    responses=_admin_write_responses("Class identity already exists"),
)
def update_class_route(
    class_id: ClassId,
    body: ClassUpdateRequest,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> ClassResponse:
    """Update a class section or its class teacher."""
    try:
        school_class = update_class(
            db,
            actor_id=current_user.id,
            class_id=class_id,
            changes=_class_changes(body),
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return to_class_response(school_class)


@classes_router.delete(
    "/{class_id}",
    response_model=ClassResponse,
    responses=_admin_read_responses("Class not found"),
)
def deactivate_class_route(
    class_id: ClassId,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> ClassResponse:
    """Deactivate a class. The row, subjects, and enrollments are kept."""
    try:
        school_class = deactivate_class(
            db,
            actor_id=current_user.id,
            class_id=class_id,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return to_class_response(school_class)


@classes_router.put(
    "/{class_id}/teacher",
    response_model=ClassResponse,
    responses=_admin_write_responses("Teacher cannot be assigned"),
)
def assign_class_teacher_route(
    class_id: ClassId,
    body: TeacherAssignmentRequest,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> ClassResponse:
    """Assign one class teacher. Assigning the current teacher changes nothing."""
    try:
        school_class = assign_class_teacher(
            db,
            actor_id=current_user.id,
            class_id=class_id,
            teacher_id=body.teacher_id,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return to_class_response(school_class)


@classes_router.delete(
    "/{class_id}/teacher",
    response_model=ClassResponse,
    responses=_admin_read_responses("Class not found"),
)
def remove_class_teacher_route(
    class_id: ClassId,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> ClassResponse:
    """Remove the class teacher. An empty assignment stays empty."""
    try:
        school_class = remove_class_teacher(
            db,
            actor_id=current_user.id,
            class_id=class_id,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return to_class_response(school_class)


@classes_router.get(
    "/{class_id}/students",
    response_model=EnrollmentListResponse,
    responses=_admin_read_responses("Class not found"),
)
def list_class_students_route(
    class_id: ClassId,
    _admin: AdminUser,
    db: DbSession,
    enrollment_status: Annotated[EnrollmentStatus | None, Query(alias="status")] = EnrollmentStatus.ACTIVE,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EnrollmentListResponse:
    """List students in a class. Active enrollments are returned unless status is set."""
    try:
        rows, total = list_class_students(
            db,
            class_id=class_id,
            status=enrollment_status,
            page=page,
            page_size=page_size,
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return EnrollmentListResponse(
        items=[to_enrollment_response(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@classes_router.post(
    "/{class_id}/students",
    response_model=EnrollmentResponse,
    responses=_admin_write_responses("Student is already enrolled"),
)
def enroll_student_route(
    class_id: ClassId,
    body: EnrollStudentRequest,
    request: Request,
    response: Response,
    current_user: AdminUser,
    db: DbSession,
) -> EnrollmentResponse:
    """Enroll a student who is not already active in a class."""
    try:
        enrollment, created = enroll_student(
            db,
            actor_id=current_user.id,
            class_id=class_id,
            student_id=body.student_id,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    if created:
        response.status_code = status.HTTP_201_CREATED
    return to_enrollment_response(enrollment)


@classes_router.post(
    "/{class_id}/students/{student_id}/move",
    response_model=EnrollmentResponse,
    responses=_admin_write_responses("Student is already enrolled in this class"),
)
def move_student_route(
    class_id: ClassId,
    student_id: StudentId,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> EnrollmentResponse:
    """Move the student's active enrollment into this class."""
    try:
        enrollment = move_student(
            db,
            actor_id=current_user.id,
            class_id=class_id,
            student_id=student_id,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return to_enrollment_response(enrollment)


@classes_router.delete(
    "/{class_id}/students/{student_id}",
    response_model=EnrollmentResponse,
    responses=_admin_read_responses("Student is not enrolled in this class"),
)
def remove_student_route(
    class_id: ClassId,
    student_id: StudentId,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> EnrollmentResponse:
    """Withdraw a student from a class. The enrollment row is kept."""
    try:
        enrollment = remove_student(
            db,
            actor_id=current_user.id,
            class_id=class_id,
            student_id=student_id,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return to_enrollment_response(enrollment)


@subjects_router.post(
    "",
    response_model=SubjectResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_admin_write_responses("Subject code already exists in the class"),
)
def create_subject_route(
    body: SubjectCreateRequest,
    request: Request,
    response: Response,
    current_user: AdminUser,
    db: DbSession,
) -> SubjectResponse:
    """Create a subject in an active class."""
    try:
        subject = create_subject(
            db,
            actor_id=current_user.id,
            class_id=body.class_id,
            name=body.name,
            code=body.code,
            teacher_id=body.teacher_id,
            is_active=body.status == RecordStatus.ACTIVE,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    response.headers["Location"] = f"/admin/subjects/{subject.id}"
    return to_subject_response(subject)


@subjects_router.get(
    "",
    response_model=SubjectListResponse,
    responses=_admin_read_responses(),
)
def list_subjects_route(
    _admin: AdminUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    account_status: Annotated[RecordStatus | None, Query(alias="status")] = None,
    class_id: Annotated[int | None, Query(ge=1)] = None,
    teacher_id: Annotated[int | None, Query(ge=1)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SubjectListResponse:
    """List subjects. Search matches name, code, and class name."""
    rows, total = list_subjects(
        db,
        search=search,
        is_active=_is_active(account_status),
        class_id=class_id,
        teacher_id=teacher_id,
        page=page,
        page_size=page_size,
    )
    return SubjectListResponse(
        items=[to_subject_response(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@subjects_router.get(
    "/{subject_id}",
    response_model=SubjectResponse,
    responses=_admin_read_responses("Subject not found"),
)
def read_subject(subject_id: SubjectId, _admin: AdminUser, db: DbSession) -> SubjectResponse:
    """Return one subject, including a deactivated offering."""
    try:
        subject = get_subject(db, subject_id)
    except AcademicServiceError as exc:
        _reject(exc)
    return to_subject_response(subject)


@subjects_router.patch(
    "/{subject_id}",
    response_model=SubjectResponse,
    responses=_admin_write_responses("Subject code already exists in the class"),
)
def update_subject_route(
    subject_id: SubjectId,
    body: SubjectUpdateRequest,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> SubjectResponse:
    """Update a subject, its class, or its teacher."""
    try:
        subject = update_subject(
            db,
            actor_id=current_user.id,
            subject_id=subject_id,
            changes=_subject_changes(body),
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return to_subject_response(subject)


@subjects_router.delete(
    "/{subject_id}",
    response_model=SubjectResponse,
    responses=_admin_read_responses("Subject not found"),
)
def deactivate_subject_route(
    subject_id: SubjectId,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> SubjectResponse:
    """Deactivate a subject. The row is kept."""
    try:
        subject = deactivate_subject(
            db,
            actor_id=current_user.id,
            subject_id=subject_id,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return to_subject_response(subject)


@subjects_router.put(
    "/{subject_id}/teacher",
    response_model=SubjectResponse,
    responses=_admin_write_responses("Teacher cannot be assigned"),
)
def assign_subject_teacher_route(
    subject_id: SubjectId,
    body: TeacherAssignmentRequest,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> SubjectResponse:
    """Assign one subject teacher. Assigning the current teacher changes nothing."""
    try:
        subject = assign_subject_teacher(
            db,
            actor_id=current_user.id,
            subject_id=subject_id,
            teacher_id=body.teacher_id,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return to_subject_response(subject)


@subjects_router.delete(
    "/{subject_id}/teacher",
    response_model=SubjectResponse,
    responses=_admin_read_responses("Subject not found"),
)
def remove_subject_teacher_route(
    subject_id: SubjectId,
    request: Request,
    current_user: AdminUser,
    db: DbSession,
) -> SubjectResponse:
    """Remove the subject teacher. An empty assignment stays empty."""
    try:
        subject = remove_subject_teacher(
            db,
            actor_id=current_user.id,
            subject_id=subject_id,
            ip_address=_client_ip(request),
        )
    except AcademicServiceError as exc:
        _reject(exc)
    return to_subject_response(subject)


@assigned_router.get(
    "/classes",
    response_model=ClassListResponse,
    responses=_teacher_read_responses(),
)
def list_assigned_classes(
    current_user: TeacherUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    academic_year: Annotated[str | None, Query(max_length=40)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ClassListResponse:
    """List active classes where this teacher is the class teacher or a subject teacher."""
    teacher = _require_profile(db, current_user.id)
    rows, total = list_classes(
        db,
        search=search,
        is_active=None,
        academic_year=academic_year,
        page=page,
        page_size=page_size,
        teacher_id=teacher.id,
    )
    return ClassListResponse(
        items=[to_class_response(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@assigned_router.get(
    "/classes/{class_id}",
    response_model=ClassResponse,
    responses=_teacher_read_responses(),
)
def read_assigned_class(
    class_id: ClassId,
    current_user: TeacherUser,
    db: DbSession,
) -> ClassResponse:
    """Return one active class assigned to this teacher."""
    teacher = _require_profile(db, current_user.id)
    try:
        school_class = get_class_for_teacher(db, class_id, teacher.id)
    except AcademicServiceError as exc:
        _reject(exc)
    return to_class_response(school_class)


@assigned_router.get(
    "/subjects",
    response_model=SubjectListResponse,
    responses=_teacher_read_responses(),
)
def list_assigned_subjects(
    current_user: TeacherUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    class_id: Annotated[int | None, Query(ge=1)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SubjectListResponse:
    """List active subjects assigned to this teacher."""
    teacher = _require_profile(db, current_user.id)
    rows, total = list_subjects(
        db,
        search=search,
        is_active=None,
        class_id=class_id,
        teacher_id=None,
        page=page,
        page_size=page_size,
        assigned_teacher_id=teacher.id,
    )
    return SubjectListResponse(
        items=[to_subject_response(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@assigned_router.get(
    "/subjects/{subject_id}",
    response_model=SubjectResponse,
    responses=_teacher_read_responses(),
)
def read_assigned_subject(
    subject_id: SubjectId,
    current_user: TeacherUser,
    db: DbSession,
) -> SubjectResponse:
    """Return one active subject assigned to this teacher."""
    teacher = _require_profile(db, current_user.id)
    try:
        subject = get_subject_for_teacher(db, subject_id, teacher.id)
    except AcademicServiceError as exc:
        _reject(exc)
    return to_subject_response(subject)


def _class_changes(body: ClassUpdateRequest) -> dict[str, object]:
    changes: dict[str, object] = {}
    fields = body.model_fields_set
    if "name" in fields:
        changes["name"] = body.name
    if "section" in fields:
        changes["section"] = body.section
    if "academic_year" in fields:
        changes["academic_year"] = body.academic_year
    if "class_teacher_id" in fields:
        changes["class_teacher_id"] = body.class_teacher_id
    if "status" in fields and body.status is not None:
        changes["is_active"] = body.status == RecordStatus.ACTIVE
    return changes


def _subject_changes(body: SubjectUpdateRequest) -> dict[str, object]:
    changes: dict[str, object] = {}
    fields = body.model_fields_set
    if "class_id" in fields:
        changes["class_id"] = body.class_id
    if "name" in fields:
        changes["name"] = body.name
    if "code" in fields:
        changes["code"] = body.code
    if "teacher_id" in fields:
        changes["teacher_id"] = body.teacher_id
    if "status" in fields and body.status is not None:
        changes["is_active"] = body.status == RecordStatus.ACTIVE
    return changes


def _is_active(account_status: RecordStatus | None) -> bool | None:
    if account_status is None:
        return None
    return account_status == RecordStatus.ACTIVE


def _require_profile(db: Session, user_id: int):
    teacher = get_teacher_for_user(db, user_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher profile not found",
        )
    return teacher


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    host = request.client.host
    return host[:45] if host else None


def _reject(exc: AcademicServiceError) -> NoReturn:
    if isinstance(exc, ClassNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found") from None
    if isinstance(exc, SubjectNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found") from None
    if isinstance(exc, TeacherNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Teacher not found") from None
    if isinstance(exc, StudentNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found") from None
    if isinstance(exc, EnrollmentNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student is not enrolled in this class",
        ) from None
    if isinstance(exc, ClassNotActive):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Class is not active") from None
    if isinstance(exc, SubjectNotActive):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Subject is not active") from None
    if isinstance(exc, TeacherNotActive):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Teacher is not active") from None
    if isinstance(exc, StudentNotActive):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Student is not active") from None
    if isinstance(exc, DuplicateClass):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A class with this name, section, and academic year already exists",
        ) from None
    if isinstance(exc, DuplicateSubjectCode):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A subject with this code already exists in the class",
        ) from None
    if isinstance(exc, DuplicateEnrollment):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Student is already enrolled in this class",
        ) from None
    if isinstance(exc, StudentEnrolledElsewhere):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Student is already enrolled in another class",
        ) from None
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Class request failed",
    ) from exc
