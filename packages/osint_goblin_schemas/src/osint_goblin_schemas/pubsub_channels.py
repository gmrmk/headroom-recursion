"""Channel-naming convention for the worker -> API SSE bridge (R-6).

L0 placement: this module is pure-string functions, no `redis` dep. Both
`apps/api` and `apps/workers` import these helpers so the channel names
stay in lockstep without either package having to import the other (they
are L4 siblings -- no peer imports per .importlinter).

Design notes for R-6 (Sprint 2 Day 11-12):
  - We use Redis pub/sub (not Streams) for the worker->API bridge.
    Trade-off: pub/sub is fire-and-forget; messages published while no
    subscriber is connected are lost. For single-investigator personal-use
    osint-goblin, reconnect-resume is a nice-to-have that we accept as a
    future enhancement (gated by an actual UX complaint, not theory).
  - Sequence ownership: the API stamps sequence on bridge-receive (i.e.
    the worker emits events WITHOUT a sequence number; the API's
    InMemoryStore.next_seq stamps it as it pushes to the per-investigation
    queue). Workers therefore cannot make ordering claims; the API is the
    single source of monotonic per-investigation sequence.
  - Channel naming: one channel per investigation. Cross-investigation
    fan-in to a single "all events" channel would force every API
    subscriber to filter every message; one-channel-per-inv lets Redis
    do the routing.
"""

from __future__ import annotations

from uuid import UUID


def events_channel(investigation_id: UUID | str) -> str:
    """The Redis pub/sub channel for an investigation's worker events.

    Format: `osint:events:{investigation_id}` (canonical UUID hyphenated).
    The `osint:` prefix avoids collision with other Redis users on the same
    instance; the `events:` infix matches the message intent (vs e.g.
    `osint:lock:` for distributed locks the M1 plan may add).
    """
    if isinstance(investigation_id, UUID):
        return f"osint:events:{investigation_id}"
    # Already a string; assume the caller passed a canonical UUID string.
    # We intentionally do NOT validate -- the caller's discipline is the
    # contract, and Pydantic validates UUID coercion at the API edge.
    return f"osint:events:{investigation_id}"
