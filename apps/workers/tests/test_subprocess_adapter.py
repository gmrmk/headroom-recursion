"""Tests for the generic subprocess adapter helper.

Uses a temp fake wrapper that emits a controlled NDJSON stream so the
test is deterministic and doesn't depend on any AGPL package being
installed.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from osint_goblin_workers.subprocess_adapter import (
    SubprocessAdapterError,
    invoke_wrapper,
    make_subprocess_adapter,
)


def _write_wrapper(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake_wrapper.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_invoke_wrapper_round_trip(tmp_path: Path) -> None:
    """Payload reaches the wrapper; NDJSON output is parsed back."""
    wrapper = _write_wrapper(
        tmp_path,
        """
        import json, sys
        payload = json.loads(sys.stdin.read())
        print(json.dumps({"event_type": "echo", "got": payload}))
        print(json.dumps({"event_type": "done"}))
        """,
    )
    events = invoke_wrapper(wrapper, {"x": 1, "y": "two"})
    assert len(events) == 2
    assert events[0]["event_type"] == "echo"
    assert events[0]["got"] == {"x": 1, "y": "two"}
    assert events[1]["event_type"] == "done"


def test_invoke_wrapper_skips_malformed_lines(tmp_path: Path) -> None:
    """Best-effort NDJSON parsing: malformed lines are dropped silently."""
    wrapper = _write_wrapper(
        tmp_path,
        """
        import json, sys
        sys.stdin.read()
        print(json.dumps({"event_type": "ok"}))
        print("not-json-this-line")
        print(json.dumps({"event_type": "ok2"}))
        print("")
        """,
    )
    events = invoke_wrapper(wrapper, {})
    assert [e["event_type"] for e in events] == ["ok", "ok2"]


def test_invoke_wrapper_raises_on_failure_with_no_events(tmp_path: Path) -> None:
    """Non-zero exit + zero events parsed -> SubprocessAdapterError bubbles."""
    wrapper = _write_wrapper(
        tmp_path,
        """
        import sys
        sys.stdin.read()
        sys.stderr.write("crash\\n")
        sys.exit(3)
        """,
    )
    with pytest.raises(SubprocessAdapterError) as exc:
        invoke_wrapper(wrapper, {})
    assert exc.value.exit_code == 3
    assert "crash" in exc.value.stderr


def test_invoke_wrapper_partial_success_appends_failure_event(tmp_path: Path) -> None:
    """Non-zero exit + some events parsed -> events returned plus a synthetic
    'adapter-failure' event so the chain records partial progress."""
    wrapper = _write_wrapper(
        tmp_path,
        """
        import json, sys
        sys.stdin.read()
        print(json.dumps({"event_type": "partial-1"}))
        print(json.dumps({"event_type": "partial-2"}))
        sys.stderr.write("died halfway\\n")
        sys.exit(7)
        """,
    )
    events = invoke_wrapper(wrapper, {})
    assert [e["event_type"] for e in events] == [
        "partial-1",
        "partial-2",
        "adapter-failure",
    ]
    assert events[-1]["exit_code"] == 7
    assert "died halfway" in events[-1]["stderr_tail"]


def test_invoke_wrapper_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        invoke_wrapper(Path("/does/not/exist/wrapper.py"), {})


def test_make_subprocess_adapter_returns_callable(tmp_path: Path) -> None:
    """Factory closure carries the wrapper path so the AdapterRegistry can
    store a single-arg callable."""
    wrapper = _write_wrapper(
        tmp_path,
        """
        import json, sys
        payload = json.loads(sys.stdin.read())
        print(json.dumps({"event_type": "ok", "echo": payload}))
        """,
    )
    adapter = make_subprocess_adapter(wrapper)
    events = adapter({"handle": "alice"})
    assert events[0]["echo"] == {"handle": "alice"}
