"""Healthz + OpenAPI sanity."""

from __future__ import annotations


def test_healthz(client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "phase": "day-8-real"}


def test_openapi_emitted(client) -> None:
    """FastAPI auto-emits OpenAPI 3.1; required for openapi-typescript codegen."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    data = r.json()
    assert data["info"]["title"] == "OSINT Goblin API"
    assert "/investigations" in data["paths"]
