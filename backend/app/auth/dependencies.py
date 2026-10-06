"""Request dependencies that authenticate a caller and check a role.

`get_current_user` is authentication: a signed, unexpired access token
must match an active user. `require_admin`, `require_teacher`,
`require_student`, and `require_staff` are authorization. A bad token
is 401. A good token for the wrong role is 403.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.tokens import TokenError, decode_access_token
from app.database.connection import get_db
from app.models.user import User, UserRole

bearer_scheme = HTTPBearer(
    auto_error=True,
    scheme_name="AccessToken",
    bearerFormat="JWT",
    description="JWT access token returned by POST /auth/login.",
)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Load the active user identified by the access token."""
    try:
        user_id = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise _unauthorized("Could not validate credentials") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("Could not validate credentials")
    return user


def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Allow the request only when the current user is an admin."""
    return _require_role(current_user, UserRole.ADMIN, "Admin access required")


def require_teacher(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Allow the request only when the current user is a teacher."""
    return _require_role(current_user, UserRole.TEACHER, "Teacher access required")


def require_student(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Allow the request only when the current user is a student."""
    return _require_role(current_user, UserRole.STUDENT, "Student access required")


def require_staff(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Allow the request only when the current user is an admin or a teacher."""
    if current_user.role not in (UserRole.ADMIN, UserRole.TEACHER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or teacher access required",
        )
    return current_user


def _require_role(user: User, role: UserRole, detail: str) -> User:
    if user.role != role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
TeacherUser = Annotated[User, Depends(require_teacher)]
StudentUser = Annotated[User, Depends(require_student)]
StaffUser = Annotated[User, Depends(require_staff)]
