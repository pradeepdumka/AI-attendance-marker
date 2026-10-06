"""Request and response models for registration, login, and the current user."""

import re
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    model_validator,
)

from app.models.user import UserRole

_PHONE_CHARACTERS = re.compile(r"[0-9+\-() ]+")


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    local, separator, domain = email.partition("@")
    if (
        not separator
        or not local
        or not domain
        or "@" in domain
        or any(character.isspace() for character in email)
    ):
        raise ValueError("Enter a valid email address")
    return email


def _normalize_person_name(value: str) -> str:
    name = " ".join(value.split())
    if name == "":
        raise ValueError("Enter a name")
    if len(name) > 100:
        raise ValueError("Name must be at most 100 characters")
    return name


def _normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    phone = value.strip()
    if phone == "":
        return None
    digits = sum(character.isdigit() for character in phone)
    if len(phone) > 20 or digits < 7 or _PHONE_CHARACTERS.fullmatch(phone) is None:
        raise ValueError("Enter a valid phone number")
    return phone


def _registration_password(value: SecretStr) -> SecretStr:
    password = value.get_secret_value()
    if password.strip() == "":
        raise ValueError("Enter a password")
    if not 8 <= len(password) <= 128:
        raise ValueError("Password must be 8 to 128 characters")
    return value


EmailAddress = Annotated[
    str,
    Field(min_length=3, max_length=255, examples=["ada@school.edu"]),
    AfterValidator(_normalize_email),
]
PersonName = Annotated[
    str,
    Field(min_length=1, max_length=120),
    AfterValidator(_normalize_person_name),
]
PhoneNumber = Annotated[
    str | None,
    Field(max_length=30),
    AfterValidator(_normalize_phone),
]
RegistrationPassword = Annotated[SecretStr, AfterValidator(_registration_password)]


class RegisterRequest(BaseModel):
    """Account details for `POST /auth/register`.

    The password is write-only. This route does not return an access token.
    """

    first_name: PersonName = Field(examples=["Ada"])
    last_name: PersonName = Field(examples=["Lovelace"])
    email: EmailAddress
    password: RegistrationPassword
    role: UserRole = Field(examples=[UserRole.STUDENT])
    phone: PhoneNumber = None

    @model_validator(mode="after")
    def password_is_not_the_email(self) -> Self:
        if self.password.get_secret_value().casefold() == self.email.casefold():
            raise ValueError("Password must not match the email address")
        return self


class LoginRequest(BaseModel):
    """Email and password submitted to `POST /auth/login`."""

    email: EmailAddress
    password: SecretStr = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """Signed access token. The plain password is never included."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds", examples=[3600])


class CurrentUserResponse(BaseModel):
    """Signed-in user. `password_hash` is not part of this contract."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    email: str
    role: UserRole
    is_active: bool
