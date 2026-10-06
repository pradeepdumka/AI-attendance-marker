"""Environment configuration.

Runtime settings come from environment variables, with an optional
`backend/.env` file. Database credentials are separate fields. The MySQL
password is never stored in source; an empty default means "no password
was provided," and a real password belongs only in the environment.
"""

from datetime import time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# backend/.env — resolved from this file so it does not depend on the shell cwd.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Settings loaded once per process via `get_settings`."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.is_file() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Attendance Marker"
    app_env: str = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: SecretStr = SecretStr("")
    mysql_database: str = "ai_attendance"

    # HS256 needs a 32-byte secret. Empty means "not configured": login and
    # protected routes fail until JWT_SECRET is set in the environment.
    # A usable secret belongs only in the environment, never in source.
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, ge=1, le=24 * 60)

    # Euclidean distance used by face_recognition. A face matches when its
    # nearest enrolled embedding is at or below this value. 0.6 is that
    # library's usual tolerance. Lower is stricter.
    face_match_threshold: float = Field(default=0.6, gt=0, le=2)

    # Active embeddings stay in memory so a live frame does not query MySQL.
    # Saving a face sample clears the cache immediately. 0 loads again on
    # the next recognition call.
    face_embedding_cache_seconds: int = Field(default=60, ge=0, le=24 * 60 * 60)

    # Calendar date and late cutoff for attendance. An empty cutoff means a
    # face check-in is PRESENT. A check-in at or after the cutoff is LATE.
    attendance_timezone: str = "UTC"
    attendance_late_after: str = ""

    @field_validator("attendance_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("attendance_timezone must be an IANA timezone name") from exc
        return value

    @field_validator("attendance_late_after")
    @classmethod
    def _late_cutoff(cls, value: str) -> str:
        text = value.strip()
        if text == "":
            return ""
        try:
            time.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("attendance_late_after must be HH:MM or empty") from exc
        return text

    @property
    def attendance_late_time(self) -> time | None:
        """Local time after which a face check-in is LATE, or None."""
        if self.attendance_late_after == "":
            return None
        return time.fromisoformat(self.attendance_late_after)

    @property
    def database_url(self) -> URL:
        """Build a PyMySQL URL from the credential fields.

        `URL.create` percent-encodes the password, so characters such as
        `@` or `:` cannot break the URL or leak through string formatting.
        """
        return URL.create(
            drivername="mysql+pymysql",
            username=self.mysql_user,
            password=self.mysql_password.get_secret_value(),
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
