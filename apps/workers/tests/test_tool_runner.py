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


def test_known_adapter_called(capsys: pytest.CaptureFixture[str]) -> None:
    payload = {
        "investigation_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "adapter_id": "echo",
        "adapter_payload": {"handle": "alice"},
    }
    tool_runner.fn(payload)
    captured = capsys.readouterr()
    assert "echo" in captured.out
    assert "alice" in captured.out


def test_invalid_payload_rejected_by_pydantic() -> None:
    """ToolRunPayload is frozen + strict; missing required fields raise."""
    with pytest.raises(Exception, match="adapter_id|validation"):
        tool_runner.fn({"investigation_id": "x"})


def test_adapter_registry_register_unregister() -> None:
    reg = AdapterRegistry()

    def _adapter(p: dict) -> list[dict]:
        return [{"k": "v"}]

    entry = reg.register("test-adapter", _adapter, in_process=True, description="test")
    assert entry.id == "test-adapter"
    assert reg.get("test-adapter") is entry
    assert "test-adapter" in reg.names()
    reg.unregister("test-adapter")
    assert reg.get("test-adapter") is None


def test_adapter_registry_duplicate_register_rejected() -> None:
    reg = AdapterRegistry()

    def _adapter(p: dict) -> list[dict]:
        return []

    reg.register("dup", _adapter)
    with pytest.raises(ValueError, match="already registered"):
        reg.register("dup", _adapter)


def test_default_echo_adapter_in_global_registry() -> None:
    entry = get_registry().get("echo")
    assert entry is not None
    assert entry.in_process is True
    result = entry.callable({"x": 1})
    assert result == [{"event_type": "tool-run-result", "payload": {"x": 1}}]


def test_tool_runner_bound_to_a_broker() -> None:
    """Smoke check: the actor decoration succeeded and a broker is attached."""
    assert tool_runner.broker is not None
    assert tool_runner.queue_name == "tool_runner"
