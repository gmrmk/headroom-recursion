"""Dramatiq broker setup.

Mode-aware (Boris D10): in production use RedisBroker; in tests use StubBroker.
The single configure_broker() call is the seam.

Win11-native operational requirement (Priya): worker processes spawned with
--processes 1 --threads 4 due to multiprocessing.spawn SIGINT propagation
issue. That's a CLI invocation concern, not a broker concern.
"""

from __future__ import annotations

import dramatiq
from dramatiq import Broker
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker


def configure_broker(*, url: str = "redis://127.0.0.1:6379/0", stub: bool = False) -> Broker:
    """Configure the global Dramatiq broker. Idempotent.

    Args:
      url: Redis broker URL (ignored if stub=True).
      stub: When True (tests), use StubBroker. When False (prod/dev), Redis.

    Returns the configured broker. Caller may attach middleware before
    use; tool_runner is registered via decorator on import so it picks up
    whatever broker is set when import happens.
    """
    broker: Broker = StubBroker() if stub else RedisBroker(url=url)
    dramatiq.set_broker(broker)
    return broker


def get_broker() -> Broker:
    """Return the currently-configured broker."""
    return dramatiq.get_broker()
