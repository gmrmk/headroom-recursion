"""Runtime AGPL containment check.

R-8 (Camille + Yuki + Sora phase6 convergence): the AST + regex lint at
tools/ci/agpl_import_lint.py catches static and dynamic imports at PR
time. This module is the third layer: a runtime assertion that walks
sys.modules at app startup and raises if any AGPL module is loaded
inside the host process (apps/api or apps/workers). Defense-in-depth.

The AGPL adapters run as subprocess children of apps/workers (per
ADR-0006). The subprocess wrappers at adapters/<id>/wrapper.py legitimately
import AGPL modules, but they run in their own Python interpreter -- the
host worker's sys.modules never sees them. If sys.modules in the host
ever contains an AGPL module, something has gone wrong:
  - a contributor bypassed the AST lint via dynamic import the regex
    layer also missed
  - a transitive dep pulled in an AGPL package without our knowledge
  - a hot-reload / test leaked module state across processes

`assert_no_agpl_loaded()` is meant to be called once at app startup. It is
not expensive (sys.modules dict scan), idempotent, and fails loudly with
the offending module names.

Lives in osint_goblin_schemas (L0) because both apps and (eventually) any
package can import it cheaply -- it has zero deps beyond stdlib.
"""

from __future__ import annotations

import sys

AGPL_FORBIDDEN: frozenset[str] = frozenset(
    {
        "bbot",
        "ghunt",
        "social_analyzer",
        "snscrape",
        "trufflehog",
        "phoneinfoga",
        "onionsearch",
        "ivre",
        "aleph",
        "spiderfoot",
    }
)


class AGPLContaminationError(RuntimeError):
    """Raised when an AGPL module is loaded in the host process.

    Distinct from a normal RuntimeError so callers can catch specifically
    (e.g., test harnesses that want to assert this fires).
    """


def loaded_agpl_modules() -> set[str]:
    """Return the set of AGPL_FORBIDDEN top-level package names currently
    present in sys.modules.

    Matches on the top-level name only (`maigret.cli` -> `maigret`).
    Returns an empty set when the process is clean.
    """
    loaded: set[str] = set()
    for module_name in sys.modules:
        top = module_name.split(".", 1)[0]
        if top in AGPL_FORBIDDEN:
            loaded.add(top)
    return loaded


def assert_no_agpl_loaded() -> None:
    """Raise AGPLContaminationError if any AGPL module is loaded.

    Call this once at app startup (apps/api/main.py + apps/workers
    entry-point). The error names which modules leaked so the operator
    can trace the bypass.
    """
    leaked = loaded_agpl_modules()
    if leaked:
        names = ", ".join(sorted(leaked))
        raise AGPLContaminationError(
            f"AGPL containment broken: forbidden modules loaded in host process: "
            f"{names}. The AST + regex lint at tools/ci/agpl_import_lint.py "
            f"should have caught this at PR time; a dynamic-import bypass or "
            f"a transitive dep must have introduced it. Run "
            f"`python tools/ci/agpl_import_lint.py` to locate the source."
        )
