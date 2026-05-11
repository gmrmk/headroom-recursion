"""Module entrypoint so `dramatiq osint_goblin_workers` works.

Discovers all @dramatiq.actor decorated functions in this package and
registers them with the broker. Priya operational requirement: CLI invocation
must use --processes 1 --threads 4 --use-spawn on Win11 (Win11 has no fork()
and 3.14 forkserver migration; phase6 Boris P0).
"""

from __future__ import annotations

from osint_goblin_schemas.agpl_runtime_check import assert_no_agpl_loaded

# R-8 phase6: third layer of AGPL containment defense-in-depth.
# The host workers process MUST NOT have AGPL modules loaded -- AGPL adapters
# run as subprocess children via adapters/<id>/wrapper.py (ADR-0006). Fires
# before broker config so a contamination error never reaches Dramatiq.
assert_no_agpl_loaded()

# Triggering imports registers actors with the current broker.
from . import tool_runner  # noqa: F401, E402
from .broker import configure_broker  # noqa: E402

# Configure broker on import so `dramatiq osint_goblin_workers` Just Works.
configure_broker()
