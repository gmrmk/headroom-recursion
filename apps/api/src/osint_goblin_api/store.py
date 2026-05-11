"""In-memory store + Redis pub/sub bridge.

Day 8 baseline: in-memory store for investigations + per-investigation
asyncio.Queue feeding the SSE handler. Real Postgres+pgvector store lands
in M1 (Diego sec.C); single-threaded asyncio access.

R-6 (Sprint 2 Day 11-12): adds a Redis pub/sub subscriber per investigation
so the worker process can publish events that flow into the SSE stream.
The API stamps `sequence` + `ts` on bridge-receive (osint_goblin_schemas
pubsub_channels module docstring explains the sequence-ownership choice).

Subscriber lifecycle:
  - A subscriber task is started on the first call to `stream(inv_id)` for
    an investigation and stopped on the last call's cleanup. Reference-
    counted across multiple SSE clients on the same investigation.
  - The subscriber reads from `osint:events:{inv_id}`, parses the JSON
    payload, stamps sequence + ts, and pushes to the local queue. The
    existing SSE generator picks it up.
  - Lost-on-disconnect: pub/sub semantics. Acceptable for single-investigator
    personal use; Streams + Last-Event-ID is a future enhancement gated by
    actual UX need (see pubsub_channels.py docstring).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import redis.asyncio as redis_async
from osint_goblin_schemas.pubsub_channels import events_channel

from .models import Investigation, InvestigationEvent

logger = logging.getLogger(__name__)

_REDIS_URL = os.environ.get("OSINT_REDIS_URL", "redis://127.0.0.1:6379/0")


class InMemoryStore:
    """Investigations + per-investigation event queues + Redis bridge.

    The bridge is opt-in via `start_bridge()`/`stop_bridge()`; tests that
    do not need it can skip the Redis dep entirely by never calling
    `start_bridge`. The route handler's `stream()` calls `_ensure_bridge`
    on first subscribe.
    """

    def __init__(self) -> None:
        self._investigations: dict[UUID, Investigation] = {}
        self._queues: dict[UUID, asyncio.Queue[InvestigationEvent]] = defaultdict(asyncio.Queue)
        self._sequence: dict[UUID, int] = defaultdict(int)
        # R-6 subscriber state
        self._bridge_refcount: dict[UUID, int] = defaultdict(int)
        self._bridge_tasks: dict[UUID, asyncio.Task[None]] = {}
        # Once a POST /run has fired, we hold one "permanent" bridge ref per
        # investigation so events published by the worker before the user's
        # EventSource opens are not lost. Memory cost: one Redis pubsub
        # subscription per investigation that has ever been run. Acceptable
        # for single-investigator personal use.
        self._bridge_persistent_holds: set[UUID] = set()
        self._redis: redis_async.Redis | None = None

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

    def _get_redis(self) -> redis_async.Redis:
        if self._redis is None:
            self._redis = redis_async.from_url(_REDIS_URL, decode_responses=True)
        return self._redis

    async def _run_bridge(self, inv_id: UUID) -> None:
        """Subscribe to osint:events:{inv_id} and forward to the local queue.

        Runs until cancelled (i.e. until the last SSE client disconnects and
        the refcount hits zero). Cancellation is the normal exit path.
        """
        channel = events_channel(inv_id)
        pubsub = self._get_redis().pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    raw = json.loads(message["data"])
                except (ValueError, KeyError) as exc:
                    logger.warning("bridge: malformed event on %s: %s", channel, exc)
                    continue
                # Stamp sequence + ts on bridge-receive (single source of
                # monotonic per-investigation sequence; see module docstring).
                raw.setdefault("ts", datetime.now(UTC).isoformat())
                raw["sequence"] = self.next_seq(inv_id)
                raw["investigation_id"] = str(inv_id)
                try:
                    event = InvestigationEvent.model_validate(raw)
                except Exception as exc:
                    logger.warning("bridge: validation failed on %s: %s", channel, exc)
                    continue
                await self.publish_event(event)
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    async def _ensure_bridge(self, inv_id: UUID) -> None:
        """Reference-counted start. Idempotent across multiple SSE clients."""
        self._bridge_refcount[inv_id] += 1
        if inv_id not in self._bridge_tasks:
            self._bridge_tasks[inv_id] = asyncio.create_task(self._run_bridge(inv_id))

    async def hold_bridge_persistent(self, inv_id: UUID) -> None:
        """Acquire one non-releasable bridge ref for an investigation.

        Called from POST /run so events emitted by the worker between the
        run-accepted moment and the SSE client's EventSource open are not
        lost to pub/sub fire-and-forget. The advisor's R-6 round-2 concern:
        in the real flow `POST /run -> worker publishes -> user navigates
        and opens EventSource` can drop the early events; this fixes it by
        attaching the subscriber at run-acceptance time instead.

        Idempotent: subsequent calls for the same investigation are no-ops.
        Once held, the bridge stays up for the lifetime of the process.
        """
        if inv_id in self._bridge_persistent_holds:
            return
        self._bridge_persistent_holds.add(inv_id)
        await self._ensure_bridge(inv_id)

    async def _release_bridge(self, inv_id: UUID) -> None:
        """Reference-counted stop. Cancels the subscriber when refcount hits 0."""
        self._bridge_refcount[inv_id] -= 1
        if self._bridge_refcount[inv_id] <= 0:
            self._bridge_refcount.pop(inv_id, None)
            task = self._bridge_tasks.pop(inv_id, None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def stream(self, inv_id: UUID) -> AsyncIterator[InvestigationEvent]:
        """Async generator over events for an investigation. Used by the SSE
        route handler. Yields heartbeat events alongside real events so the
        connection stays alive. R-6: starts/stops the Redis pub/sub bridge
        for the lifetime of this generator."""
        await self._ensure_bridge(inv_id)
        q = self._queues[inv_id]
        try:
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
        finally:
            await self._release_bridge(inv_id)
