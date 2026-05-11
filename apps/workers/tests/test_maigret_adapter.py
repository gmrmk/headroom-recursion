"""Maigret adapter end-to-end test.

Invokes the real adapters/maigret/wrapper.py via the registry, asserts the
wire-contract shape: started + N site-hits + complete event sequence.
Synthetic-mode fallback ensures this passes on machines without the AGPL
maigret package installed.
"""

from __future__ import annotations

from osint_goblin_workers.adapters import get_registry


def test_maigret_registered() -> None:
    entry = get_registry().get("maigret")
    assert entry is not None
    assert entry.in_process is False, "AGPL adapter must be subprocess-isolated"
    assert "AGPL" in entry.description


def test_maigret_emits_expected_event_shape() -> None:
    """End-to-end: invoke the real wrapper via subprocess, observe contract."""
    entry = get_registry().get("maigret")
    assert entry is not None
    events = entry.callable({"username": "alice"})

    assert len(events) >= 3, "expected started + >=1 hit + complete"
    assert events[0]["event_type"] == "tool-run-started"
    assert events[0]["adapter"] == "maigret"
    assert events[0]["username"] == "alice"

    hits = [e for e in events if e["event_type"] == "site-hit"]
    assert len(hits) >= 1, "expected at least one site-hit"
    for h in hits:
        assert "site" in h
        assert "url" in h
        assert "alice" in h["url"], "username should appear in synthetic URLs"

    assert events[-1]["event_type"] == "tool-run-complete"
    assert events[-1]["adapter"] == "maigret"
    assert events[-1]["hits"] == len(hits)


def test_maigret_synthetic_mode_emits_same_contract_shape() -> None:
    """Yuki P1 (phase6): synthetic_mode is a separate callable that bypasses
    the live import attempt and produces the same wire contract -- so the
    M0 exit gate can exercise the pipeline without maigret installed."""
    entry = get_registry().get("maigret")
    assert entry is not None
    assert entry.synthetic_mode is not None

    events = entry.synthetic_mode({"username": "bob"})

    # Same contract as the live path
    assert events[0]["event_type"] == "tool-run-started"
    assert events[0]["username"] == "bob"
    hits = [e for e in events if e["event_type"] == "site-hit"]
    assert len(hits) >= 1
    assert all("bob" in h["url"] for h in hits)
    assert events[-1]["event_type"] == "tool-run-complete"
    # The wrapper marks events as synthetic when running in that mode.
    assert events[-1].get("synthetic") is True
