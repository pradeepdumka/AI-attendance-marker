"""Routes that only an admin may call.

`GET /admin/me` identifies the signed-in admin. Student roster routes
live under `/admin/students`. Teacher profile routes live under
`/admin/teachers`. Class and subject routes live under `/admin/classes`
and `/admin/subjects`.
"""

from fastapi import APIRouter

from app.auth.dependencies import AdminUser
from app.models.user import User
from app.schemas.auth import CurrentUserResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    responses={
        401: {"description": "Missing, expired, or invalid access token"},
        403: {"description": "Signed in as a teacher or a student"},
    },
)
def read_admin(current_user: AdminUser) -> User:
    """Return the current user when that user is an admin."""
    return current_user
