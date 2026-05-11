"""Unit tests for tool_runner + adapter registry.

Day 8 scope: actor body (tool_runner.fn) + AdapterRegistry contract.
The full broker round-trip (send -> worker.process -> result) is exercised
in Day 9 against a live Memurai-backed Dramatiq worker (WI-0205 + the M0
exit gate). StubBroker.join() without a Worker hangs, so we avoid it.
"""

from __future__ import annotations

import uuid

import pytest
from osint_goblin_workers.adapters import AdapterRegistry, get_registry
from osint_goblin_workers.tool_runner import tool_runner


def test_unknown_adapter_raises() -> None:
    payload = {
        "investigation_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "adapter_id": "does-not-exist",
        "adapter_payload": {},
    }
    with pytest.raises(ValueError, match="unknown adapter"):
        tool_runner.fn(payload)


def test_known_adapter_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_runner dispatches to the named adapter and publishes each event
    via the worker -> API Redis pub/sub bridge (R-6 Sprint 2 Day 11-12).
    We mock the publisher so this stays a unit test (no Redis needed)."""
    published: list[tuple[str, dict]] = []

    def fake_publish(inv_id: str, event: dict) -> int:
        published.append((inv_id, event))
        return 1  # subscriber count

    # The package __init__ binds the Actor on `osint_goblin_workers.tool_runner`
    # attribute name (shadowing the submodule reference for `import as` syntax),
    # so grab the actual submodule via sys.modules.
    import sys

    tr_mod = sys.modules["osint_goblin_workers.tool_runner"]
    monkeypatch.setattr(tr_mod, "publish_event", fake_publish)

    inv = str(uuid.uuid4())
    run = str(uuid.uuid4())
    tool_runner.fn(
        {
            "investigation_id": inv,
            "run_id": run,
            "adapter_id": "echo",
            "adapter_payload": {"handle": "alice"},
        }
    )
    assert len(published) == 1
    pub_inv, pub_event = published[0]
    assert pub_inv == inv
    assert pub_event["event_type"] == "tool-run-result"
    assert pub_event["payload"] == {"handle": "alice"}
    assert pub_event["investigation_id"] == inv
    assert pub_event["run_id"] == run


def test_invalid_payload_rejected_by_pydantic() -> None:
    """ToolRunPayload is frozen + strict; missing required fields raise."""
    with pytest.raises(Exception, match="adapter_id|validation"):
        tool_runner.fn({"investigation_id": "x"})


def test_adapter_registry_register_unregister() -> None:
    reg = AdapterRegistry()

    def _adapter(p: dict) -> list[dict]:
        return [{"k": "v"}]

    entry = reg.register(
        "test-adapter",
        _adapter,
        synthetic_mode=_adapter,
        in_process=True,
        description="test",
    )
    assert entry.id == "test-adapter"
    assert reg.get("test-adapter") is entry
    assert "test-adapter" in reg.names()
    reg.unregister("test-adapter")
    assert reg.get("test-adapter") is None


def test_adapter_registry_duplicate_register_rejected() -> None:
    reg = AdapterRegistry()

    def _adapter(p: dict) -> list[dict]:
        return []

    reg.register("dup", _adapter, synthetic_mode=_adapter)
    with pytest.raises(ValueError, match="already registered"):
        reg.register("dup", _adapter, synthetic_mode=_adapter)


def test_default_echo_adapter_in_global_registry() -> None:
    entry = get_registry().get("echo")
    assert entry is not None
    assert entry.in_process is True
    assert entry.synthetic_mode is not None
    result = entry.callable({"x": 1})
    assert result == [{"event_type": "tool-run-result", "payload": {"x": 1}}]
    # Pure in-process: live == synthetic (echo doesn't touch the network)
    assert entry.synthetic_mode is entry.callable


# ----------------------------------------------------------------------------
# Yuki P1 (phase6 2026-05-11): synthetic_mode mandate
# ----------------------------------------------------------------------------


def test_synthetic_mode_is_required_at_register_time() -> None:
    """Adapters without synthetic_mode must fail registration, not at M0 gate."""
    reg = AdapterRegistry()

    def _adapter(p: dict) -> list[dict]:
        return []

    with pytest.raises(ValueError, match="synthetic_mode is mandatory"):
        reg.register("missing-synthetic", _adapter, synthetic_mode=None)


def test_synthetic_mode_field_exposed_on_entry() -> None:
    """AdapterEntry has a `synthetic_mode` attribute; not None for any
    registered adapter."""
    reg = AdapterRegistry()

    def _live(p: dict) -> list[dict]:
        return [{"event_type": "live"}]

    def _syn(p: dict) -> list[dict]:
        return [{"event_type": "synthetic"}]

    entry = reg.register("split", _live, synthetic_mode=_syn)
    assert entry.callable is _live
    assert entry.synthetic_mode is _syn
    # The two paths are independent
    assert entry.callable({})[0]["event_type"] == "live"
    assert entry.synthetic_mode({})[0]["event_type"] == "synthetic"


def test_registry_assert_all_have_synthetic_mode_passes_for_clean_registry() -> None:
    """Registry-level lint: callable from CI to fail the build if any adapter
    is missing synthetic_mode."""
    reg = AdapterRegistry()

    def _a(p: dict) -> list[dict]:
        return []

    reg.register("a1", _a, synthetic_mode=_a)
    reg.register("a2", _a, synthetic_mode=_a)
    # Does not raise
    reg.assert_all_have_synthetic_mode()


def test_global_registry_passes_synthetic_mode_lint() -> None:
    """The actual production registry must pass the lint -- every adapter
    registered at import time has a synthetic_mode."""
    get_registry().assert_all_have_synthetic_mode()


def test_tool_runner_bound_to_a_broker() -> None:
    """Smoke check: the actor decoration succeeded and a broker is attached."""
    assert tool_runner.broker is not None
    assert tool_runner.queue_name == "tool_runner"
