"""Request and response models for face enrollment.

The response reports whether a student has active face samples. It does
not include the embedding or any image.
"""

import enum
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.face_encoding import FaceEncoding


class FaceEnrollmentStatus(enum.Enum):
    """Whether the student has at least one active face sample."""

    NOT_ENROLLED = "NOT_ENROLLED"
    ENROLLED = "ENROLLED"


class FaceSampleResponse(BaseModel):
    """One stored sample. The embedding itself is not returned."""

    sample_id: int
    created_at: datetime


class FaceEnrollmentResponse(BaseModel):
    """Active face samples for one student."""

    student_id: int
    status: FaceEnrollmentStatus
    sample_count: int = Field(ge=0)
    samples: list[FaceSampleResponse]


def to_face_enrollment(
    student_id: int,
    samples: list[FaceEncoding],
) -> FaceEnrollmentResponse:
    """Build the public enrollment status from active sample rows."""
    return FaceEnrollmentResponse(
        student_id=student_id,
        status=FaceEnrollmentStatus.ENROLLED if samples else FaceEnrollmentStatus.NOT_ENROLLED,
        sample_count=len(samples),
        samples=[
            FaceSampleResponse(sample_id=sample.id, created_at=sample.created_at)
            for sample in samples
        ],
    )
