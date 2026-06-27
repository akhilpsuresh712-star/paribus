"""Pydantic request/response models for the bulk-processing API."""
from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class RowStatus(str, Enum):
    CREATED_AND_ACTIVATED = "created_and_activated"
    CREATED = "created"  # created but activation skipped/failed
    FAILED = "failed"


class HospitalRow(BaseModel):
    """A single validated CSV row to be created upstream."""

    row: int
    name: str
    address: str
    phone: str | None = None


class RowError(BaseModel):
    """A row that failed CSV-level validation (before any upstream call)."""

    row: int
    error: str


class HospitalResult(BaseModel):
    """Per-row outcome in the final report."""

    row: int
    hospital_id: int | None = None
    name: str
    status: RowStatus
    error: str | None = None


class BatchReport(BaseModel):
    """The synchronous response shape for POST /hospitals/bulk."""

    batch_id: UUID
    total_hospitals: int
    processed_hospitals: int
    failed_hospitals: int
    processing_time_seconds: float
    batch_activated: bool
    hospitals: list[HospitalResult]


class ValidationResponse(BaseModel):
    """Response for the validate-only endpoint."""

    valid: bool
    total_rows: int
    valid_rows: int
    invalid_rows: int
    rows: list[HospitalRow] = Field(default_factory=list)
    errors: list[RowError] = Field(default_factory=list)


class BatchStatus(BaseModel):
    """Polling view backed by the in-memory store."""

    batch_id: UUID
    status: str  # running | completed | failed
    total: int
    processed: int
    failed: int
    activated: bool
