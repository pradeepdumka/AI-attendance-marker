"""Declarative base for ORM models.

The class lives in `app.models.base` so every table shares one metadata
registry next to the models that use it. This module re-exports that class
for imports that still refer to the database package.
"""

from app.models.base import Base

__all__ = ["Base"]
