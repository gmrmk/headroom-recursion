"""Worker -> API SSE bridge publisher (R-6 Sprint 2 Day 11-12).

The worker process publishes adapter event dicts onto a Redis pub/sub
channel named per-investigation. The API process subscribes to those
channels on the SSE handler entry and forwards messages to its in-process
per-investigation queue, where existing SSE machinery picks them up.

Why pub/sub and not Streams: see osint_goblin_schemas.pubsub_channels.

Worker process responsibility (this module):
  - Serialize the adapter event dict to JSON.
  - PUBLISH to `osint:events:{investigation_id}`.
  - The worker does NOT stamp `sequence` or `ts`; the API does that on
    bridge-receive so per-investigation monotonicity is owned by exactly
    one process.

The redis client is module-scoped so the worker doesn't pay TCP setup on
every event. Dramatiq workers are long-lived; a single connection lasts
the lifetime of the worker process.
"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import UUID

import redis
from osint_goblin_schemas.pubsub_channels import events_channel

# Lazy module-level singleton -- created on first publish, reused for the
# lifetime of the worker process.
_REDIS_URL = os.environ.get("OSINT_REDIS_URL", "redis://127.0.0.1:6379/0")
_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(_REDIS_URL, decode_responses=True)
    return _client


def publish_event(investigation_id: UUID | str, event: dict[str, Any]) -> int:
    """Publish one adapter event to the API subscriber.

    Args:
      investigation_id: target investigation.
      event: dict matching the InvestigationEvent contract MINUS
        `sequence` and `ts`. The API stamps both on bridge-receive.

    Returns:
      Number of subscribers that received the message. 0 means no API
      process was listening; the message is lost (pub/sub semantics).
      Caller may log this for visibility but should not treat 0 as a
      hard error -- the M0 contract accepts that events published before
      a subscriber connects are not replayed.
    """
    channel = events_channel(investigation_id)
    payload = json.dumps(event, default=str)  # default=str for UUID/datetime
    return _get_client().publish(channel, payload)


def reset_client_for_tests() -> None:
    """Test-only seam: drop the cached client so tests can swap connection
    parameters or mock the module-level redis dep."""
    global _client
    _client = None
