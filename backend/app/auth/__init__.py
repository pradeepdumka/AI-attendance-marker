"""Password hashing, JWT access tokens, and role checks.

Authentication answers who is calling. Authorization answers which role
that person may use. Route modules depend on `get_current_user`,
`require_admin`, `require_teacher`, `require_student`, and `require_staff`.
"""

from app.auth.dependencies import (
    get_current_user,
    require_admin,
    require_staff,
    require_student,
    require_teacher,
)
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import create_access_token, decode_access_token

__all__ = [
    "create_access_token",
    "decode_access_token",
    "get_current_user",
    "hash_password",
    "require_admin",
    "require_staff",
    "require_student",
    "require_teacher",
    "verify_password",
]
