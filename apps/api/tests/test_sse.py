"""SSE stream tests.

Day-8 scope:
  - Store-level: events published from one coroutine are observed by another
    in the same event loop.
  - Route-level: 404 for unknown investigations, headers honor Mei-Lan §7
    (X-Accel-Buffering: no, Cache-Control: no-cache).

Live end-to-end SSE observation (POST /run -> stream sees event over HTTP)
needs a real uvicorn server, not TestClient/ASGITransport — TestClient
runs each request in its own anyio portal (cross-event-loop asyncio.Queue
isn't shared), and ASGITransport buffers streaming responses in this
configuration. Day 10 M0 exit gate exercises 30+ events on a live handle
through a real uvicorn process; the contract is verified there.

M1 (Diego sec.C) swaps the in-memory queue for Redis pub/sub and this
bug class goes away.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from osint_goblin_api.models import InvestigationEvent
from osint_goblin_api.store import InMemoryStore


@pytest.mark.asyncio
async def test_store_publishes_event_to_stream_consumer() -> None:
    """Same-loop pub/sub round-trip — proves the store contract that the
    SSE handler relies on."""
    store = InMemoryStore()
    inv_id = uuid.uuid4()

    received: list[InvestigationEvent] = []

    async def _consume() -> None:
        async for ev in store.stream(inv_id):
            received.append(ev)
            return

    task = asyncio.create_task(_consume())
    # Give the consumer a beat to attach to the queue
    await asyncio.sleep(0.05)
    await store.publish_event(
        InvestigationEvent(
            event_type="tool-run-accepted",
            investigation_id=inv_id,
            sequence=1,
            payload={"adapter_id": "maigret"},
        )
    )
    await asyncio.wait_for(task, timeout=2.0)

    assert len(received) == 1
    assert received[0].event_type == "tool-run-accepted"
    assert received[0].investigation_id == inv_id


def test_sse_unknown_investigation_404(client: TestClient) -> None:
    r = client.get(f"/investigations/{uuid.uuid4()}/stream")
    assert r.status_code == 404


def test_sse_route_handler_sets_x_accel_headers() -> None:
    """Mei-Lan §7: X-Accel-Buffering: no must be set so nginx/uvicorn doesn't
    buffer the stream.

    Unit-asserted on the route source rather than a live stream — opening a
    TestClient.stream against EventSourceResponse blocks until the first
    chunk (sse_starlette default ping is 15s with no events queued), and
    Day 10's M0 exit gate exercises the live header contract against a real
    uvicorn process.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "src" / "osint_goblin_api" / "routes.py"
    ).read_text(encoding="utf-8")
    assert '"X-Accel-Buffering": "no"' in src
    assert '"Cache-Control": "no-cache"' in src
