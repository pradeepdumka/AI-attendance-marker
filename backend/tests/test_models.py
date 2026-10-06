"""Schema checks for the ORM models. These tests do not need MySQL."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import class_mapper
from sqlalchemy.schema import CreateIndex, CreateTable

from app.database.base import Base as DatabaseBase
from app.models import (
    Attendance,
    AttendanceMethod,
    AttendanceStatus,
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
from app.models.base import UtcDateTime


def _mysql_ddl(table) -> str:
    dialect = mysql.dialect()
    statements = [str(CreateTable(table).compile(dialect=dialect))]
    statements.extend(str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes)
    return "\n".join(statements)


def test_database_package_reexports_the_model_base() -> None:
    assert DatabaseBase is Base


def test_expected_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "students",
        "teachers",
        "classes",
        "subjects",
        "enrollments",
        "face_encodings",
        "attendance_records",
        "audit_logs",
    }


def test_user_identity_columns_and_unique_email_index() -> None:
    columns = User.__table__.c

    assert columns.id.primary_key
    assert columns.email.nullable is False
    assert columns.email.type.length == 255
    assert columns.password_hash.nullable is False
    assert columns.password_hash.type.length == 255
    assert "password" not in columns

    email_indexes = [index for index in User.__table__.indexes if index.name == "ix_users_email"]
    assert len(email_indexes) == 1
    assert email_indexes[0].unique is True
    assert [column.name for column in email_indexes[0].columns] == ["email"]

    assert columns.is_active.nullable is False
    assert columns.is_active.default.arg is True
    assert {role.value for role in UserRole} == {"ADMIN", "TEACHER", "STUDENT"}
    assert list(columns.role.type.enums) == ["ADMIN", "TEACHER", "STUDENT"]


def test_no_table_stores_a_plain_text_password() -> None:
    for table in Base.metadata.tables.values():
        assert "password" not in table.c
        assert "password_hash" not in table.c or table.name == "users"


def test_user_profiles_are_optional_one_to_one() -> None:
    relationships = class_mapper(User).relationships

    assert relationships["student"].uselist is False
    assert relationships["teacher"].uselist is False
    assert class_mapper(Student).relationships["user"].uselist is False
    assert class_mapper(Teacher).relationships["user"].uselist is False

    assert Student.__table__.c.user_id.nullable is False
    assert Student.__table__.c.user_id.unique is True
    assert Teacher.__table__.c.user_id.nullable is False
    assert Teacher.__table__.c.user_id.unique is True
    assert Teacher.__table__.c.employee_code.nullable is False
    assert Teacher.__table__.c.employee_code.unique is True


def test_attendance_is_unique_per_student_subject_and_date() -> None:
    constraint_names = {constraint.name for constraint in Attendance.__table__.constraints}
    assert "uq_attendance_student_subject_date" in constraint_names
    assert list(Attendance.__table__.c.status.type.enums) == [
        status.value for status in AttendanceStatus
    ]
    assert list(Attendance.__table__.c.method.type.enums) == [
        method.value for method in AttendanceMethod
    ]


def test_student_gender_is_optional() -> None:
    gender = Student.__table__.c.gender
    assert gender.nullable is True
    assert gender.default is None
    assert Student.__table__.c.roll_number.unique is True
    assert Student.__table__.c.date_of_birth.nullable is True
    assert list(gender.type.enums) == ["FEMALE", "MALE", "OTHER"]


def test_enrollment_status_defaults_to_active() -> None:
    status = Enrollment.__table__.c.status
    assert status.nullable is False
    assert status.default.arg is EnrollmentStatus.ACTIVE
    assert list(status.type.enums) == ["ACTIVE", "COMPLETED", "WITHDRAWN"]


def test_class_identity_is_unique_for_a_year() -> None:
    names = {constraint.name for constraint in SchoolClass.__table__.constraints}
    assert "uq_classes_name_section_year" in names
    assert SchoolClass.__tablename__ == "classes"


def test_subject_code_is_unique_inside_a_class() -> None:
    names = {constraint.name for constraint in Subject.__table__.constraints}
    assert "uq_subjects_class_id_code" in names


def test_face_encoding_belongs_to_a_student_and_stores_json() -> None:
    column = FaceEncoding.__table__.c.encoding
    assert column.nullable is False
    assert column.type.__class__.__name__ == "JSON"
    foreign_key = next(iter(FaceEncoding.__table__.c.student_id.foreign_keys))
    assert foreign_key.ondelete == "CASCADE"


def test_audit_log_actor_is_optional_and_details_are_not_named_metadata() -> None:
    assert AuditLog.__table__.c.actor_id.nullable is True
    assert "details" in AuditLog.__table__.c
    assert "metadata" not in AuditLog.__table__.c
    foreign_key = next(iter(AuditLog.__table__.c.actor_id.foreign_keys))
    assert foreign_key.ondelete == "SET NULL"


def test_user_ddl_uses_utc_timestamps_and_hashes_passwords() -> None:
    ddl = _mysql_ddl(User.__table__)

    assert "password_hash" in ddl
    assert "password " not in ddl.lower()
    assert "VARCHAR(255) NOT NULL" in ddl
    assert "ENUM('ADMIN','TEACHER','STUDENT')" in ddl
    assert "DEFAULT (UTC_TIMESTAMP())" in ddl
    assert "ON UPDATE CURRENT_TIMESTAMP" in ddl
    assert "UNIQUE INDEX ix_users_email" in ddl or "UNIQUE INDEX `ix_users_email`" in ddl


def test_utc_datetime_normalizes_to_utc_and_rejects_naive_values() -> None:
    column_type = UtcDateTime()
    dialect = mysql.dialect()
    india = timezone(timedelta(hours=5, minutes=30))

    stored = column_type.process_bind_param(
        datetime(2026, 10, 5, 17, 30, tzinfo=india),
        dialect,
    )
    assert stored == datetime(2026, 10, 5, 12, 0)

    with pytest.raises(ValueError, match="timezone-aware"):
        column_type.process_bind_param(datetime(2026, 10, 5, 12, 0), dialect)

    loaded = column_type.process_result_value(datetime(2026, 10, 5, 12, 0), dialect)
    assert loaded == datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
