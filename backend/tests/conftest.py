"""Reset cached settings and the MySQL pool around each test."""

import pytest

from app.config import get_settings
from app.database.connection import dispose_engine


@pytest.fixture(autouse=True)
def reset_database_and_settings():
    get_settings.cache_clear()
    dispose_engine()
    yield
    get_settings.cache_clear()
    dispose_engine()
