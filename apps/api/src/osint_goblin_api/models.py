"""Pydantic v2 models for the API surface.

Diego phase3/04-backend-data-engineer.md sec.B1 baseline; trimmed to what
Day 8 needs. Larger schemas (Evidence, Claim, LawfulBasisAttestation) land
in later WIs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

SubjectType = Literal["username", "email", "phone", "domain", "person", "image", "face", "event"]


class Subject(BaseModel):
    """The primary identifier an investigation is about."""

    model_config = ConfigDict(frozen=True)

    kind: SubjectType
    value: str


class CreateInvestigation(BaseModel):
    """POST body for creating a new investigation."""

    subject: Subject
    investigator_handle: str = Field(min_length=1, max_length=64)
    notes: str = Field(default="", max_length=2048)


class Investigation(BaseModel):
    """Read model."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    subject: Subject
    investigator_handle: str
    notes: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolRunRequest(BaseModel):
    """POST body for kicking off a tool_runner job."""

    adapter_id: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


class ToolRunResponse(BaseModel):
    """Response from queue-acceptance (idempotency key + the canonical run id).

    The actual results stream via SSE on /investigations/{id}/stream.
    """

    model_config = ConfigDict(frozen=True)

    run_id: UUID = Field(default_factory=uuid4)
    investigation_id: UUID
    adapter_id: str
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InvestigationEvent(BaseModel):
    """A single SSE event payload. Drives the dossier UI."""

    model_config = ConfigDict(frozen=True)

    event_type: Literal[
        "heartbeat",
        "capture-started",
        "warc-written",
        "ed25519-signed",
        "rfc3161-stamped",
        "minio-stored",
        "ftm-entity-created",
        "wayback-queued",
        "tool-run-accepted",
        "tool-run-result",
        "tool-run-error",
        # R-5 phase6 property-vetting adapters (Sprint 2 / 2026-05-11):
        # domain-meaningful event types for the six-primitive triangulation.
        # See apps/workers/src/osint_goblin_workers/adapters_property.py.
        "geocode-match",  # Nominatim address -> lat/lon
        "listing-match",  # Inside Airbnb / lodging platform hit
        "person-match",  # TruePeopleSearch / PII lookup hit
        "breach-hit",  # HIBP / credential breach finding
        "image-match",  # TinEye / reverse-image hit
    ]
    investigation_id: UUID
    run_id: UUID | None = None
    sequence: int
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict = Field(default_factory=dict)
