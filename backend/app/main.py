"""Application entrypoint.

Builds the FastAPI app from environment settings and mounts routers.
Startup does not open MySQL; the health route checks the database on
demand. Authentication routes issue and require JWT access tokens.
Admins manage the student roster at `/admin/students`, teacher
profiles at `/admin/teachers`, and classes and subjects at
`/admin/classes` and `/admin/subjects`. Admins and teachers enroll
faces at `/students/{student_id}/face`. Staff mark attendance from a
camera frame at `POST /attendance/mark`. Teachers read their own
classes, subjects, students, attendance, and reports at `/teacher`.
Students read their own profile, class, subjects, and attendance at
`/student`.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth.tokens import AuthConfigurationError
from app.config import get_settings
from app.database.connection import dispose_engine
from app.routers.attendance import router as attendance_router
from app.routers.academics import (
    assigned_router,
    classes_router,
    subjects_router,
)
from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.faces import router as faces_router
from app.routers.health import router as health_router
from app.routers.pages import ASSETS_DIR
from app.routers.pages import router as pages_router
from app.routers.student import router as student_router
from app.routers.students import router as students_router
from app.routers.teacher import router as teacher_router
from app.routers.teachers import router as teachers_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Return pooled MySQL connections to the server when the process stops."""
    yield
    dispose_engine()


def create_app() -> FastAPI:
    """Create a new application instance. Tests call this directly."""
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    @app.exception_handler(AuthConfigurationError)
    def authentication_not_configured(
        _request: Request,
        exc: AuthConfigurationError,
    ) -> JSONResponse:
        logger.error("Authentication is not configured: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Authentication is not configured"},
        )

    @app.exception_handler(RequestValidationError)
    def validation_error_hides_passwords(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Return the usual 422 body with password inputs removed."""
        return JSONResponse(
            status_code=422,
            content={"detail": jsonable_encoder(_redact_validation_errors(exc))},
        )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(students_router)
    app.include_router(teachers_router)
    app.include_router(classes_router)
    app.include_router(subjects_router)
    app.include_router(teacher_router)
    app.include_router(assigned_router)
    app.include_router(student_router)
    app.include_router(faces_router)
    app.include_router(attendance_router)
    app.include_router(pages_router)
    if ASSETS_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
    return app


def _redact_validation_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    redacted: list[dict[str, object]] = []
    for error in exc.errors():
        item = dict(error)
        if "input" in item:
            location = item.get("loc", ())
            if "password" in location:
                item["input"] = "***"
            else:
                item["input"] = _redact_password_fields(item["input"])
        redacted.append(item)
    return redacted


def _redact_password_fields(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "***" if key == "password" else _redact_password_fields(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_password_fields(nested) for nested in value]
    return value


app = create_app()
