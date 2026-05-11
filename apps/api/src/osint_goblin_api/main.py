"""FastAPI app entrypoint.

Diego sec.B1 + Mei-Lan §7 SSE wiring. The route module is the surface; this
file is the tiny composition root + middleware.
"""

from __future__ import annotations

from fastapi import FastAPI

from .routes import router

app = FastAPI(
    title="OSINT Goblin API",
    version="2026.05.0",
    description="Greenfield FOSS-first OSINT investigation dashboard backend.",
)

app.include_router(router)
