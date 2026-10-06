"""Registration, login, and the current-user route.

`POST /auth/register` creates an active student, teacher, or admin account.
`POST /auth/login` checks the password hash and returns a JWT access
token. `GET /auth/me` requires that token and returns the active user.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import CurrentUser
from app.auth.tokens import assert_auth_configured, create_access_token
from app.config import get_settings
from app.database.connection import get_db
from app.models.user import User
from app.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
)
from app.services.auth import EmailAlreadyRegistered, authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/register",
    response_model=CurrentUserResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "An account with this email already exists"}},
)
def register(body: RegisterRequest, db: DbSession) -> User:
    """Create an active user. Sign in afterward with `POST /auth/login`."""
    try:
        return register_user(
            db,
            first_name=body.first_name,
            last_name=body.last_name,
            email=body.email,
            password=body.password.get_secret_value(),
            role=body.role,
            phone=body.phone,
        )
    except EmailAlreadyRegistered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from None


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        401: {"description": "Unknown email, wrong password, or inactive account"},
        500: {"description": "JWT_SECRET is not configured"},
    },
)
def login(body: LoginRequest, db: DbSession) -> TokenResponse:
    """Issue an access token when the email and password match an active user."""
    assert_auth_configured()
    user = authenticate_user(db, body.email, body.password.get_secret_value())
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(user_id=user.id),
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    responses={401: {"description": "Missing, expired, or invalid access token"}},
)
def read_current_user(current_user: CurrentUser) -> User:
    """Return the active user identified by the access token."""
    return current_user
