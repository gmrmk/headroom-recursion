"""Maigret subprocess wrapper -- AGPL-3.0 boundary.

Sora ADR-0004 + Camille AGPL containment: this file is the ONLY place
`import maigret` is permitted; tools/ci/agpl_import_lint.py path-exempts
adapters/<id>/wrapper.py.

Contract:
  stdin:  one JSON object on a single line then EOF
            {"username": "<handle>", "timeout": 30, "tags": ["any"]}
  stdout: NDJSON event objects, one per line. Event types:
            {"event_type": "tool-run-started",  "adapter": "maigret",
             "started_at": "<iso8601>", "username": "<handle>"}
            {"event_type": "site-hit", "site": "...", "url": "...",
             "category": "..."}
            {"event_type": "tool-run-complete", "adapter": "maigret",
             "duration_s": <float>, "hits": <int>}
  stderr: free-form log lines (captured by the actor, not parsed)
  exit:   0 on clean completion; non-zero on adapter failure

If `maigret` is not importable (dev machine without it installed), the
wrapper emits a synthetic event stream so the contract is verifiable
even on greenfield machines. Real maigret invocation lands when the
AGPL containment story is signed off; the wire shape is the M1 contract.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_maigret_synthetic(payload: dict) -> int:
    """Synthetic mode -- emits a realistic event stream without invoking
    maigret. Used on machines where maigret is not installed. The wire
    shape matches what the real adapter would produce."""
    username = payload.get("username", "")
    started = time.monotonic()
    _emit(
        {
            "event_type": "tool-run-started",
            "adapter": "maigret",
            "started_at": _now_iso(),
            "username": username,
        }
    )
    # A small fixed set of synthetic site-hits so M0 exit-gate event count
    # is deterministic; real maigret may emit hundreds.
    synthetic_sites = [
        ("GitHub", f"https://github.com/{username}", "coding"),
        ("Reddit", f"https://reddit.com/user/{username}", "social"),
        ("Twitter", f"https://twitter.com/{username}", "social"),
        ("HackerNews", f"https://news.ycombinator.com/user?id={username}", "news"),
        ("Keybase", f"https://keybase.io/{username}", "identity"),
    ]
    hits = 0
    for site, url, cat in synthetic_sites:
        _emit(
            {
                "event_type": "site-hit",
                "site": site,
                "url": url,
                "category": cat,
            }
        )
        hits += 1
    _emit(
        {
            "event_type": "tool-run-complete",
            "adapter": "maigret",
            "duration_s": round(time.monotonic() - started, 3),
            "hits": hits,
            "synthetic": True,
        }
    )
    return 0


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        sys.stderr.write("error: empty stdin payload\n")
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"error: invalid JSON payload: {exc}\n")
        return 2

    # Yuki P1 (phase6): explicit synthetic-mode env-var lets the M0 exit gate
    # exercise the wire contract without ever attempting to import maigret.
    # Honors `OSINT_ADAPTER_MODE=synthetic` (the registry uses this for the
    # synthetic_mode entry-point per adapters.py).
    import os
    if os.environ.get("OSINT_ADAPTER_MODE") == "synthetic":
        sys.stderr.write("OSINT_ADAPTER_MODE=synthetic; bypassing import attempt\n")
        return _run_maigret_synthetic(payload)

    try:
        # The only AGPL import in the entire codebase (path-exempt).
        import maigret  # noqa: F401  -- real path; falls through to synthetic if missing
    except ImportError:
        sys.stderr.write("maigret not installed; using synthetic mode\n")
        return _run_maigret_synthetic(payload)

    # Real maigret invocation lands here. For now, synthetic so the wire
    # contract is exercised end-to-end without requiring AGPL install.
    sys.stderr.write("maigret available but not yet wired; using synthetic\n")
    return _run_maigret_synthetic(payload)


if __name__ == "__main__":
    raise SystemExit(main())
