"""ORM models for users, classes, enrollment, attendance, and audit.

Importing this package registers every table on `Base.metadata` and
configures relationships. It does not open a database connection.
"""

from sqlalchemy.orm import configure_mappers

from app.models.attendance import Attendance, AttendanceMethod, AttendanceStatus
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.class_model import SchoolClass
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.face_encoding import FaceEncoding
from app.models.student import Gender, Student
from app.models.subject import Subject
from app.models.teacher import Teacher
from app.models.user import User, UserRole

configure_mappers()

__all__ = [
    "Attendance",
    "AttendanceMethod",
    "AttendanceStatus",
    "AuditLog",
    "Base",
    "Enrollment",
    "EnrollmentStatus",
    "FaceEncoding",
    "Gender",
    "SchoolClass",
    "Student",
    "Subject",
    "Teacher",
    "User",
    "UserRole",
]
