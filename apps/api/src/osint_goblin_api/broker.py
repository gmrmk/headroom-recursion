"""Dramatiq broker for the API's enqueue path (Sprint 3 wire).

The API does NOT import osint_goblin_workers (DAG: workers + api are L4
siblings; siblings cannot peer-import). Instead it talks to the same
Redis broker the worker subscribes to, and enqueues tool_runner messages
by actor name + queue name strings. The worker's @dramatiq.actor
decorator on tool_runner binds the actor to those strings; the API
constructs a Message with matching strings and dramatiq routes it.

This module is the single seam between API code and dramatiq. Test
seam: callers may patch `get_broker` to substitute a StubBroker.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.message import Message

_REDIS_URL = os.environ.get("OSINT_REDIS_URL", "redis://127.0.0.1:6379/0")
_broker: dramatiq.Broker | None = None


def get_broker() -> dramatiq.Broker:
    """Lazy module-level singleton. First call configures dramatiq's
    global broker so any later actor decorations would attach correctly
    (though the API does NOT define actors)."""
    global _broker
    if _broker is None:
        _broker = RedisBroker(url=_REDIS_URL)
        dramatiq.set_broker(_broker)
    return _broker


def enqueue_tool_run(
    investigation_id: str,
    run_id: str,
    adapter_id: str,
    adapter_payload: dict[str, Any],
) -> str:
    """Enqueue a `tool_runner` message on the broker. Returns the
    Dramatiq message id.

    The shape matches osint_goblin_workers.tool_runner.tool_runner --
    actor_name='tool_runner', queue_name='tool_runner', single dict
    positional arg with the ToolRunPayload fields.
    """
    broker = get_broker()
    msg = Message(
        queue_name="tool_runner",
        actor_name="tool_runner",
        args=(
            {
                "investigation_id": investigation_id,
                "run_id": run_id,
                "adapter_id": adapter_id,
                "adapter_payload": adapter_payload,
            },
        ),
        kwargs={},
        options={},
        message_id=str(uuid4()),
        message_timestamp=0,  # broker stamps this
    )
    broker.enqueue(msg)
    return msg.message_id


def reset_broker_for_tests() -> None:
    """Drop the cached broker so tests can swap config."""
    global _broker
    _broker = None
