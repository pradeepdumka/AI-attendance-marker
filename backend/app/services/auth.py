"""Create accounts and check logins against the stored password hash.

Registration writes an Argon2id hash and never a plain password. Login
uses the plain password only for the Argon2 check. Unknown emails still
run a hash verification so the response time does not reveal whether
the account exists.
"""

from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password, verify_password
from app.models.user import User, UserRole

# Not a credential for any account. Used only so a missing user still
# pays the Argon2 verification cost.
_DUMMY_PASSWORD = "dummy-password-not-a-user-secret"


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    return hash_password(_DUMMY_PASSWORD)


class EmailAlreadyRegistered(Exception):
    """A user row already uses this email address."""


def register_user(
    db: Session,
    *,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    role: UserRole,
    phone: str | None,
) -> User:
    """Insert an active user and return the stored row.

    `password` is hashed before insert and is not kept on the instance.
    A duplicate email, including a race on the unique index, raises
    `EmailAlreadyRegistered`.
    """
    normalized = email.strip().lower()
    if _find_user_by_email(db, normalized) is not None:
        raise EmailAlreadyRegistered
    user = User(
        first_name=first_name,
        last_name=last_name,
        email=normalized,
        password_hash=hash_password(password),
        phone=phone,
        role=role,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _is_duplicate_email(exc):
            raise EmailAlreadyRegistered from None
        raise
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Return the active user when the email and password match.

    Inactive accounts, unknown emails, and wrong passwords all return
    `None`. Callers respond with the same 401 either way.
    """
    user = _find_user_by_email(db, email)
    stored_hash = user.password_hash if user is not None else _dummy_password_hash()
    password_ok = verify_password(password, stored_hash)
    if user is None or not password_ok or not user.is_active:
        return None
    return user


def _find_user_by_email(db: Session, email: str) -> User | None:
    normalized = email.strip().lower()
    return db.scalar(select(User).where(func.lower(User.email) == normalized))


def _is_duplicate_email(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return "email" in message or "ix_users_email" in message
