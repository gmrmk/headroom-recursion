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

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

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


@router.post("/investigations/{inv_id}/run", status_code=202)
async def run_tool(inv_id: UUID, body: ToolRunRequest) -> ToolRunResponse:
    store = get_store()
    inv = store.get(inv_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="investigation not found")
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
