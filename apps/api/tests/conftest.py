"""Shared FastAPI TestClient fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from osint_goblin_api.main import app
from osint_goblin_api.routes import get_store
from osint_goblin_api.store import InMemoryStore


@pytest.fixture
def fresh_store() -> InMemoryStore:
    """Each test gets a fresh in-memory store (no cross-test leakage)."""
    store = InMemoryStore()
    app.dependency_overrides[get_store] = lambda: store
    yield store
    app.dependency_overrides.clear()


@pytest.fixture
def client(fresh_store: InMemoryStore) -> TestClient:
    return TestClient(app)
