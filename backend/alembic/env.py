"""Alembic environment.

The database URL is the application URL from `app.config`, so migrations
use the same MySQL credentials as the API. Importing `app.models` registers
every table on `Base.metadata` for autogenerate. Offline mode renders SQL
without opening a connection.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _set_sqlalchemy_url() -> None:
    """Point Alembic at the configured database without writing the password to disk.

    ConfigParser treats `%` as interpolation. Passwords and URL-encoded
    characters are escaped so a value such as `%40` survives.
    """
    url = get_settings().database_url.render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))


def run_migrations_offline() -> None:
    """Render SQL to stdout. `alembic upgrade head --sql` uses this path."""
    _set_sqlalchemy_url()
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations on a short-lived connection pinned to UTC."""
    _set_sqlalchemy_url()
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={
            "connect_timeout": 5,
            "init_command": "SET time_zone = '+00:00'",
        },
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
