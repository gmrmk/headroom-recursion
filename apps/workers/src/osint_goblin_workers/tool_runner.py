"""The single dispatch actor for all M1 tool adapters.

Sora ADR-0004 + Diego sec.B2: rather than N actors per tool, one actor with
an adapter registry. New tools = new registry entry, not a new actor.

Win11 operational note (Priya): the dramatiq CLI invocation MUST pass
--processes 1 --threads 4 due to multiprocessing.spawn SIGINT propagation.
"""

from __future__ import annotations

from typing import Annotated

import dramatiq
from pydantic import BaseModel, ConfigDict, Field

from .adapters import get_registry


class ToolRunPayload(BaseModel):
    """Payload accepted by tool_runner. Strict validation; idempotency key
    is the (investigation_id, run_id) tuple; the actor dedups by run_id."""

    model_config = ConfigDict(frozen=True)

    investigation_id: str
    run_id: str
    adapter_id: Annotated[str, Field(min_length=1, max_length=64)]
    adapter_payload: dict = Field(default_factory=dict)


@dramatiq.actor(
    queue_name="tool_runner",
    max_retries=2,
    time_limit=300_000,  # 5min wall ceiling per job
)
def tool_runner(req: dict) -> None:
    """Single-entry dispatch.

    Parses `req` into a ToolRunPayload, looks up the adapter in the registry,
    calls it, then (in M1) pushes the result events through the evidence
    pipeline. Day 8: events bubble up via the in-memory store on the API side
    (the API publishes synthesized accepted events; this actor is invoked but
    we don't yet wire its results back to SSE -- that's Day 9 + 10).

    Idempotency: the (investigation_id, run_id) tuple is the natural dedup
    key; actual dedup happens in Diego's M1 forensic_log INSERT (UNIQUE
    constraint on idempotency_key). Day 8 just makes the contract callable.
    """
    payload = ToolRunPayload.model_validate(req)
    entry = get_registry().get(payload.adapter_id)
    if entry is None:
        raise ValueError(f"unknown adapter: {payload.adapter_id!r}")
    # In Day 9+ this dispatches through the evidence pipeline. For now,
    # call the adapter directly; the events it returns go to a structured
    # log (a real result emitter is WI-0203 follow-up).
    events = entry.callable(payload.adapter_payload)
    # The full pipeline (Merkle append, Ed25519 sign, RFC3161 stamp, MinIO,
    # FtM, SSE notify, Wayback) lands in WI-0202 + WI-0203 integration.
    # Day 8 ships the dispatch shape; the chain emission is Day 9 + 10.
    for ev in events:
        # Print for log-driven observability until SSE wire-back lands.
        print(f"[tool_runner] adapter={payload.adapter_id!r} event={ev!r}")
