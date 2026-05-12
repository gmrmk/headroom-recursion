"""Module entrypoint so `dramatiq osint_goblin_workers` works.

Discovers all @dramatiq.actor decorated functions in this package and
registers them with the broker. Priya operational requirement: CLI invocation
must use --processes 1 --threads 4 --use-spawn on Win11 (Win11 has no fork()
and 3.14 forkserver migration; phase6 Boris P0).

Logless contract (target-data-handling-policy.md): we silence loggers
that would write target-bearing strings to disk. Specifically `httpx`
INFO-level logs every outbound URL (e.g. /lookup?email=alice@... or
/profiles/<sha256-of-email>). Suppress to WARNING -- only errors land in
logs. Same treatment for `urllib3` and `dramatiq` (we keep WARN+ for
crash visibility).
"""

from __future__ import annotations

import logging

# Logless contract: silence per-request URL logging BEFORE any module
# import that might trigger an httpx call. Setting the logger level here
# is the cheapest gate -- handlers added later inherit the threshold.
for _name in ("httpx", "httpcore", "urllib3", "dramatiq", "redis"):
    logging.getLogger(_name).setLevel(logging.WARNING)
# Root logger: errors only by default (the worker's own intentional
# stderr writes still print as-is).
logging.getLogger().setLevel(logging.WARNING)

from osint_goblin_schemas.agpl_runtime_check import assert_no_agpl_loaded  # noqa: E402

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
