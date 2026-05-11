"""In-memory store for Day 8. Real Postgres+pgvector store lands in M1
(Diego sec.C). Single-threaded asyncio access; the broker carries cross-
process events via Redis.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from uuid import UUID

from .models import Investigation, InvestigationEvent


class InMemoryStore:
    """Investigations + per-investigation event queues.

    Each investigation has a dedicated asyncio.Queue that route handlers
    read from to deliver SSE events. Workers/external producers push events
    via `publish_event`. Day-8 only; the M1 store uses Postgres LISTEN/NOTIFY
    + Redis pub/sub.
    """

    def __init__(self) -> None:
        self._investigations: dict[UUID, Investigation] = {}
        self._queues: dict[UUID, asyncio.Queue[InvestigationEvent]] = defaultdict(asyncio.Queue)
        self._sequence: dict[UUID, int] = defaultdict(int)

    def create(self, inv: Investigation) -> Investigation:
        self._investigations[inv.id] = inv
        return inv

    def get(self, inv_id: UUID) -> Investigation | None:
        return self._investigations.get(inv_id)

    def list_all(self) -> list[Investigation]:
        return list(self._investigations.values())

    def next_seq(self, inv_id: UUID) -> int:
        self._sequence[inv_id] += 1
        return self._sequence[inv_id]

    async def publish_event(self, event: InvestigationEvent) -> None:
        """Push an event onto the investigation's queue (workers call this)."""
        await self._queues[event.investigation_id].put(event)

    async def stream(self, inv_id: UUID) -> AsyncIterator[InvestigationEvent]:
        """Async generator over events for an investigation. Used by the SSE
        route handler. Yields heartbeat events alongside real events so the
        connection stays alive."""
        q = self._queues[inv_id]
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                yield event
            except TimeoutError:
                # Heartbeat keeps the connection alive in front of proxies
                seq = self.next_seq(inv_id)
                yield InvestigationEvent(
                    event_type="heartbeat",
                    investigation_id=inv_id,
                    sequence=seq,
                )
