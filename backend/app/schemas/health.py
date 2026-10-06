"""Response schema for the health check."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Payload returned by `GET /health`."""

    status: str = Field(examples=["ok"])
    app: str = Field(examples=["AI Attendance Marker"])
    environment: str = Field(examples=["development"])
    database: str = Field(examples=["connected"])
