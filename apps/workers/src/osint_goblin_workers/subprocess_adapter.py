"""Generic subprocess-adapter helper.

Sora ADR-0004 + Camille AGPL containment: every AGPL-licensed third-party
tool runs in an out-of-namespace subprocess (adapters/<id>/wrapper.py). This
module is the shared dispatch surface — one function that takes a wrapper
path + a JSON-serializable payload and returns the wrapper's NDJSON output
parsed into a list of event dicts.

Contract (Sora ADR-0004 sec.5):
  stdin:  one JSON object (the adapter payload) on a single line, then EOF
  stdout: zero-or-more NDJSON event objects (one per line)
  stderr: free-form log lines (captured but not parsed)
  exit:   0 = success; non-zero = adapter failure (events still parsed
          best-effort up to the failure point so partial progress is visible)

Timeouts honor the actor's wall ceiling (tool_runner.time_limit = 5min); a
local timeout below that is the failure-mode budget for THIS adapter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


class SubprocessAdapterError(Exception):
    """Raised when the wrapper subprocess fails in a way that should bubble
    up to the actor (non-zero exit AND zero parseable events)."""

    def __init__(self, wrapper: str, exit_code: int, stderr: str) -> None:
        self.wrapper = wrapper
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(
            f"subprocess adapter {wrapper!r} exited {exit_code}; stderr (tail): {stderr[-400:]!r}"
        )


def _parse_ndjson(stream: str) -> list[dict]:
    """Parse NDJSON output into a list of event dicts. Best-effort: malformed
    lines are skipped (the wrapper may emit partial events before crashing)."""
    events: list[dict] = []
    for line in stream.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def invoke_wrapper(
    wrapper_path: Path | str,
    payload: dict,
    *,
    timeout_s: float = 120.0,
    python_executable: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> list[dict]:
    """Run `<python> <wrapper_path>`, pipe `payload` as JSON on stdin, parse
    stdout NDJSON, return events.

    Raises SubprocessAdapterError on non-zero exit when zero events parsed.
    A non-zero exit with at least one parsed event is treated as "partial
    success" — events returned, but a synthetic 'adapter-failure' event is
    appended so the chain records the failure.
    """
    wrapper_path = Path(wrapper_path)
    if not wrapper_path.is_file():
        raise FileNotFoundError(f"wrapper not found: {wrapper_path}")

    cmd = [python_executable or sys.executable, str(wrapper_path)]
    stdin_payload = json.dumps(payload, separators=(",", ":")) + "\n"

    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)

    proc = subprocess.run(
        cmd,
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        env=env,
        check=False,
    )

    events = _parse_ndjson(proc.stdout)

    if proc.returncode != 0:
        if not events:
            raise SubprocessAdapterError(
                wrapper=str(wrapper_path),
                exit_code=proc.returncode,
                stderr=proc.stderr or "",
            )
        events.append(
            {
                "event_type": "adapter-failure",
                "wrapper": str(wrapper_path),
                "exit_code": proc.returncode,
                "stderr_tail": (proc.stderr or "")[-1000:],
            }
        )

    return events


def make_subprocess_adapter(
    wrapper_path: Path | str,
    *,
    timeout_s: float = 120.0,
    extra_env: dict[str, str] | None = None,
    python_executable: str | None = None,
):
    """Factory: return a callable conforming to AdapterCallable that dispatches
    to the wrapper at `wrapper_path`. The callable closes over the path so
    registry entries don't have to carry it.

    `extra_env` is forwarded to invoke_wrapper -- useful for synthetic-mode
    factories that need to pass `OSINT_ADAPTER_MODE=synthetic` (Yuki P1 phase6).

    `python_executable` lets the adapter pin a specific interpreter when the
    wrapper depends on packages outside the worker's own venv (e.g. Scrapling
    + Patchright living in the empirical venv at
    `osint-dashboard-research/empirical/.venv`). Defaults to `sys.executable`
    (the worker's interpreter) when None.
    """
    resolved_wrapper = Path(wrapper_path)
    resolved_env = dict(extra_env) if extra_env else None
    resolved_python = python_executable

    def _adapter(payload: dict) -> list[dict]:
        return invoke_wrapper(
            resolved_wrapper,
            payload,
            timeout_s=timeout_s,
            extra_env=resolved_env,
            python_executable=resolved_python,
        )

    return _adapter
