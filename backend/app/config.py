"""Environment configuration.

Runtime settings come from environment variables, with an optional
`backend/.env` file. Database credentials are separate fields. The MySQL
password is never stored in source; an empty default means "no password
was provided," and a real password belongs only in the environment.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
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
