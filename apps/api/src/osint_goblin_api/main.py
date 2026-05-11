"""FastAPI app entrypoint.

Diego sec.B1 + Mei-Lan section 7 SSE wiring. The route module is the surface;
this file is the tiny composition root + middleware.
"""

from __future__ import annotations

from fastapi import FastAPI
from osint_goblin_schemas.agpl_runtime_check import assert_no_agpl_loaded

from .files import router as files_router
from .routes import router

# R-8 phase6: third layer of AGPL containment defense-in-depth (after the AST
# lint and the regex dynamic-import scanner). Fires at import time so a CI
# smoke test that just `import osint_goblin_api.main` catches a regression
# before the app even starts.
assert_no_agpl_loaded()

app = FastAPI(
    title="OSINT Goblin API",
    version="2026.05.0",
    description="Greenfield FOSS-first OSINT investigation dashboard backend.",
)

app.include_router(router)
app.include_router(files_router)
