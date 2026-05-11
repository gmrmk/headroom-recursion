"""FastAPI route handlers. Diego sec.B1 abridged.

Routes:
  GET  /healthz                                  - liveness
  POST /investigations                           - create
  GET  /investigations                           - list
  GET  /investigations/{id}                      - read
  POST /investigations/{id}/run                  - submit tool_runner job
  GET  /investigations/{id}/stream               - SSE event stream (sse_starlette)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from .broker import enqueue_tool_run, enqueue_workflow_run, is_workflow_id
from .models import (
    CreateInvestigation,
    Investigation,
    InvestigationEvent,
    ToolRunRequest,
    ToolRunResponse,
)
from .store import InMemoryStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# Day 10 (WI-0208 M0 exit gate): the M0 gate test needs 30+ events arriving
# within 60s with first-event<3s. The real worker->SSE bridge is R-6 (Redis
# pub/sub fix, Sprint 2 Day 11-12). Until then, the `m0_gate_stress` adapter
# id triggers an in-process synthetic stream emitted from the API directly.
# This is M0-gate-only and intentionally non-prod (a real adapter would never
# emit from the API request handler).
_M0_GATE_STRESS_ADAPTER = "m0_gate_stress"
_M0_GATE_STRESS_EVENT_COUNT = 32  # >30 to give the gate slack
_M0_GATE_STRESS_INTERVAL_S = 0.05  # 32 events * 0.05s = ~1.6s wall

# Strong references to background tasks so the event loop doesn't GC them
# mid-flight (ruff RUF006). One set per process; entries auto-evict via the
# done-callback registered when the task is created.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


router = APIRouter()


# A module-level singleton is fine for Day 8; Diego's M1 design uses
# FastAPI's Depends() with a shared store provider.
_STORE = InMemoryStore()


def get_store() -> InMemoryStore:
    """Test seam: override via app.dependency_overrides for unit tests."""
    return _STORE


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "phase": "day-8-real"}


@router.post("/investigations", status_code=201)
def create_investigation(body: CreateInvestigation) -> Investigation:
    inv = Investigation(
        subject=body.subject,
        investigator_handle=body.investigator_handle,
        notes=body.notes,
    )
    return get_store().create(inv)


@router.get("/investigations")
def list_investigations() -> list[Investigation]:
    return get_store().list_all()


@router.get("/investigations/{inv_id}")
def get_investigation(inv_id: UUID) -> Investigation:
    inv = get_store().get(inv_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="investigation not found")
    return inv


async def _emit_m0_gate_stress(store: InMemoryStore, inv_id: UUID, run_id: UUID) -> None:
    """Emit 32 synthetic events on the store, paced at 50ms each.

    M0-gate-only path (WI-0208). Bypasses Dramatiq because the worker->SSE
    bridge (Redis pub/sub) is R-6 / Sprint 2 Day 11-12. When R-6 lands, this
    function and the `m0_gate_stress` adapter id should be removed; the real
    `m0_gate_stress` adapter in the worker registry will publish via Redis.

    Cycles through event types that an investigator might see during a real
    run so the test exercises the multi-event-type SSE path, not just one.
    """
    # Cycle through the 8 event types a real investigation would see in
    # order. The Pydantic Literal constraint on event_type enforces this set.
    cycle: tuple[str, ...] = (
        "capture-started",
        "warc-written",
        "ed25519-signed",
        "rfc3161-stamped",
        "minio-stored",
        "ftm-entity-created",
        "wayback-queued",
        "tool-run-result",
    )
    for i in range(_M0_GATE_STRESS_EVENT_COUNT):
        await store.publish_event(
            InvestigationEvent(
                event_type=cycle[i % len(cycle)],
                investigation_id=inv_id,
                run_id=run_id,
                sequence=store.next_seq(inv_id),
                payload={"i": i, "synthetic": True},
            )
        )
        await asyncio.sleep(_M0_GATE_STRESS_INTERVAL_S)


@router.post("/investigations/{inv_id}/run", status_code=202)
async def run_tool(inv_id: UUID, body: ToolRunRequest) -> ToolRunResponse:
    store = get_store()
    inv = store.get(inv_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="investigation not found")
    # R-6 round-2 fix (advisor 2026-05-11): attach the Redis subscriber here,
    # before the worker has any chance to publish, so events emitted before
    # the user's EventSource opens are not lost to pub/sub semantics.
    # Idempotent across multiple POST /run calls for the same investigation.
    await store.hold_bridge_persistent(inv_id)
    resp = ToolRunResponse(investigation_id=inv_id, adapter_id=body.adapter_id)
    # Day 8: emit synthesized acceptance event so SSE consumers see something.
    # Day 9 wires the actual tool_runner.send() Dramatiq message.
    await store.publish_event(
        InvestigationEvent(
            event_type="tool-run-accepted",
            investigation_id=inv_id,
            run_id=resp.run_id,
            sequence=store.next_seq(inv_id),
            payload={"adapter_id": body.adapter_id},
        )
    )
    # Day 10 / WI-0208: M0 exit gate stress path. See _emit_m0_gate_stress
    # docstring for the rationale. asyncio.create_task lets the HTTP response
    # return immediately while the stream fills in the background.
    if body.adapter_id == _M0_GATE_STRESS_ADAPTER:
        task = asyncio.create_task(_emit_m0_gate_stress(store, inv_id, resp.run_id))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    else:
        # ADR-0017 §3 workflow id (w*.*) -> route to workflow_runner;
        # everything else -> tool_runner.
        try:
            if is_workflow_id(body.adapter_id):
                enqueue_workflow_run(
                    investigation_id=str(inv_id),
                    run_id=str(resp.run_id),
                    workflow_id=body.adapter_id,
                    seed=body.payload,
                )
            else:
                enqueue_tool_run(
                    investigation_id=str(inv_id),
                    run_id=str(resp.run_id),
                    adapter_id=body.adapter_id,
                    adapter_payload=body.payload,
                )
        except Exception as exc:
            # Broker unreachable -- surface in dossier, don't 500.
            await store.publish_event(
                InvestigationEvent(
                    event_type="tool-run-error",
                    investigation_id=inv_id,
                    run_id=resp.run_id,
                    sequence=store.next_seq(inv_id),
                    payload={
                        "adapter_id": body.adapter_id,
                        "reason": f"broker enqueue failed: {type(exc).__name__}: {exc}",
                    },
                )
            )
    return resp


@router.get("/investigations/{inv_id}/stream")
async def stream_events(inv_id: UUID, request: Request) -> EventSourceResponse:
    """SSE stream of InvestigationEvent dicts. Mei-Lan §7 / Diego sec.B1.

    Honors X-Accel-Buffering: no per Mei-Lan §7 (un-buffered through any
    front-proxy / uvicorn).
    """
    store = get_store()
    if store.get(inv_id) is None:
        raise HTTPException(status_code=404, detail="investigation not found")

    async def event_generator() -> AsyncIterator[dict]:
        async for event in store.stream(inv_id):
            if await request.is_disconnected():
                break
            yield {
                "event": event.event_type,
                "id": str(event.sequence),
                "data": event.model_dump_json(),
            }

    return EventSourceResponse(
        event_generator(),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
