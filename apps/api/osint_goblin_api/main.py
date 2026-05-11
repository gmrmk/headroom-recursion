"""Day-1 placeholder FastAPI app. Replace with the real entrypoint
(packages/osint_goblin_api per Sora sec3.1) once Sprint-1 lands the real
modules. This stub exists only so start-dev.ps1's health check is meaningful
on the very first fresh-clone boot."""
from fastapi import FastAPI

app = FastAPI(title="osint-goblin (placeholder)")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "phase": "day-1-placeholder"}