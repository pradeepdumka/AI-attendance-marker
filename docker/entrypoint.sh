#!/bin/sh
set -eu

cd /app/backend

echo "Waiting for MySQL at ${MYSQL_HOST:-localhost}:${MYSQL_PORT:-3306}..."
python - <<'PY'
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.config import get_settings

settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 5},
)
deadline = time.monotonic() + 90
while True:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        break
    except OperationalError:
        if time.monotonic() >= deadline:
            print("MySQL did not become ready in time.", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)
engine.dispose()
print("MySQL is ready.")
PY

echo "Applying database migrations..."
alembic upgrade head

echo "Starting API on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
