"""Module entrypoint so `dramatiq osint_goblin_workers` works.

Discovers all @dramatiq.actor decorated functions in this package and
registers them with the broker. Priya operational requirement: CLI invocation
must use --processes 1 --threads 4 on Win11 (multiprocessing.spawn SIGINT).
"""

from __future__ import annotations

# Triggering imports registers actors with the current broker.
from . import tool_runner  # noqa: F401
from .broker import configure_broker

# Configure broker on import so `dramatiq osint_goblin_workers` Just Works.
configure_broker()
