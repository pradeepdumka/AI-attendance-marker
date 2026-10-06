"""Signed JWT access tokens.

A token answers who the caller claims to be (`sub`) and when that claim
expires (`exp`). It does not grant a role. Role checks load the user from
the database after the signature is valid.
"""

from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings

# HMAC-SHA256 key length recommended for HS256.
_MIN_SECRET_BYTES = 32
_ALLOWED_ALGORITHMS = frozenset({"HS256"})


class AuthConfigurationError(Exception):
    """JWT settings are missing or too weak to sign or check tokens."""


class TokenError(Exception):
    """The bearer token is missing, expired, or not a valid access token."""


def assert_auth_configured() -> None:
    """Raise when the process cannot sign access tokens."""
    _signing_key()


def create_access_token(*, user_id: int, expires_delta: timedelta | None = None) -> str:
    """Sign an access token for `user_id`.

    The lifetime comes from `ACCESS_TOKEN_EXPIRE_MINUTES` unless
    `expires_delta` is passed. The payload identifies the user and does
    not include a password or a role.
    """
    secret, algorithm = _signing_key()
    now = datetime.now(timezone.utc)
    if expires_delta is None:
        expires_delta = timedelta(minutes=get_settings().access_token_expire_minutes)
    expire = now + expires_delta
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=algorithm)
    if isinstance(token, bytes):
        return token.decode("ascii")
    return token


def decode_access_token(token: str) -> int:
    """Return the user id from a valid, unexpired access token."""
    secret, algorithm = _signing_key()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            options={"require": ["exp", "sub", "iat"]},
        )
    except jwt.InvalidTokenError as exc:
        raise TokenError("Could not validate credentials") from exc
    if payload.get("type") != "access":
        raise TokenError("Could not validate credentials")
    return _user_id(payload.get("sub"))


def _signing_key() -> tuple[str, str]:
    settings = get_settings()
    secret = settings.jwt_secret.get_secret_value()
    algorithm = settings.jwt_algorithm
    if len(secret.encode("utf-8")) < _MIN_SECRET_BYTES:
        raise AuthConfigurationError(
            "JWT_SECRET must be set in the environment to at least 32 bytes"
        )
    if algorithm not in _ALLOWED_ALGORITHMS:
        raise AuthConfigurationError("JWT_ALGORITHM must be HS256")
    return secret, algorithm


def _user_id(subject: object) -> int:
    if isinstance(subject, bool) or not isinstance(subject, str):
        raise TokenError("Could not validate credentials")
    try:
        user_id = int(subject)
    except ValueError as exc:
        raise TokenError("Could not validate credentials") from exc
    if user_id < 1:
        raise TokenError("Could not validate credentials")
    return user_id
