"""Health route.

`GET /health` reports process configuration and checks that MySQL accepts
a query on the configured database. The process can start while MySQL is
down; this route then responds with HTTP 503.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.connection import (
    DatabaseConnectionError,
    check_database_connection,
    get_db,
)
from app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

DbSession = Annotated[Session, Depends(get_db)]


def _health_body(*, status: str, database: str) -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status=status,
        app=settings.app_name,
        environment=settings.app_env,
        database=database,
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
)
def health(db: DbSession) -> HealthResponse | JSONResponse:
    """Confirm the process is up and MySQL accepts a query."""
    try:
        check_database_connection(db)
    except (SQLAlchemyError, DatabaseConnectionError) as exc:
        logger.warning("Database health check failed: %s", exc)
        body = _health_body(status="unavailable", database="disconnected")
        return JSONResponse(status_code=503, content=body.model_dump())
    return _health_body(status="ok", database="connected")
