"""Live smoke for W11.em+ Deep Email workflow.

Calls each step of W11.em+ in-process against a real email, prints the
events, and shows per-adapter timings. Skips the Dramatiq + SSE plumbing
since those are unit-tested separately -- this script's job is to verify
each of the six adapters returns sane data against the real upstream.

Usage:
  python tools/dev/smoke-w11-em.py <email>
  python tools/dev/smoke-w11-em.py <email> --only gravatar,hudson_rock
  python tools/dev/smoke-w11-em.py <email> --skip hibp,user_scanner

Each adapter falls back to its synthetic mode (or returns a clear
tool-run-error) if an upstream auth/install is missing. Configure
optional env vars before running:

  OSINT_GRAVATAR_TOKEN   bearer token (raises 100/hr -> 1000/hr)
  OSINT_GITHUB_PAT       GitHub PAT (raises 10/min -> 30/min)
  OSINT_INTELBASE_API_KEY  IntelBase key (W2.em not W11.em+, but registry-exposed)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

# Allow `python tools/dev/smoke-w11-em.py` from repo root by appending the
# worker's src dir to sys.path; uv-managed editable installs already cover
# this, but a fresh clone may not.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKER_SRC = os.path.join(_REPO_ROOT, "apps", "workers", "src")
if _WORKER_SRC not in sys.path:
    sys.path.insert(0, _WORKER_SRC)


# W11.em+ step order (matches workflows.py "w11.em").
_W11_STEPS: tuple[str, ...] = (
    "email_mx_validate",
    "hibp_breach_check",
    "gravatar_profile_lookup",
    "github_commit_email_search",
    "hudson_rock_email_check",
    "user_scanner",
)


def _color(s: str, c: str) -> str:
    """ANSI color helper. Windows Terminal handles ANSI fine."""
    codes = {
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "cyan": "\033[36m",
        "grey": "\033[90m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }
    return f"{codes.get(c, '')}{s}{codes['reset']}"


def _summarize_event(event: dict[str, Any]) -> str:
    et = event.get("event_type", "?")
    payload = event.get("payload", {})
    color = {
        "tool-run-error": "red",
        "tool-run-result": "green",
        "person-match": "cyan",
        "breach-hit": "yellow",
        "image-match": "yellow",
        "geocode-match": "yellow",
        "listing-match": "yellow",
        "tool-run-accepted": "grey",
        "adapter-failure": "red",
    }.get(et, "blue")
    summary = json.dumps(payload, separators=(",", ":"))
    if len(summary) > 140:
        summary = summary[:137] + "..."
    return f"  {_color(et, color)}  {_color(summary, 'grey')}"


def _run_adapter(adapter_id: str, payload: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    # Import inside fn so the worker package's side-effect registrations run
    # before we ask the registry for entries.
    from osint_goblin_workers.adapters import get_registry

    registry = get_registry()
    entry = registry.get(adapter_id)
    if entry is None:
        return (
            0.0,
            [
                {
                    "event_type": "tool-run-error",
                    "payload": {"reason": f"adapter '{adapter_id}' not registered"},
                }
            ],
        )

    start = time.monotonic()
    try:
        events = entry.callable(payload)
    except Exception as exc:
        events = [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"adapter raised {type(exc).__name__}: {exc}"},
            }
        ]
    elapsed = time.monotonic() - start
    return (elapsed, events or [])


def main() -> int:
    ap = argparse.ArgumentParser(description="Live smoke for W11.em+ Deep Email workflow")
    ap.add_argument("email", help="Email to look up across the W11.em+ chain")
    ap.add_argument(
        "--only",
        default="",
        help="Comma-separated adapter ids to include (default: all six W11 steps)",
    )
    ap.add_argument(
        "--skip",
        default="",
        help="Comma-separated adapter ids to skip",
    )
    args = ap.parse_args()

    email = args.email.strip()
    if "@" not in email:
        print(_color(f"ERR: not an email: {email}", "red"))
        return 2

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    steps = [s for s in _W11_STEPS if (not only or s in only) and s not in skip]

    print(_color(f"W11.em+ smoke against {email}", "bold"))
    print(_color(f"steps: {', '.join(steps)}", "grey"))
    print()

    totals = {"events": 0, "errors": 0, "person_match": 0, "breach_hit": 0}
    per_adapter: list[tuple[str, float, dict[str, int]]] = []

    for adapter_id in steps:
        print(_color(f"-- {adapter_id}", "bold"))
        elapsed, events = _run_adapter(adapter_id, {"email": email})
        for ev in events:
            print(_summarize_event(ev))
        counts = {
            "events": len(events),
            "errors": sum(1 for e in events if e.get("event_type") == "tool-run-error"),
            "person_match": sum(1 for e in events if e.get("event_type") == "person-match"),
            "breach_hit": sum(1 for e in events if e.get("event_type") == "breach-hit"),
        }
        for k, v in counts.items():
            totals[k] = totals[k] + v
        per_adapter.append((adapter_id, elapsed, counts))
        print(_color(f"  ({len(events)} events in {elapsed:.2f}s)", "grey"))
        print()

    print(_color("Summary", "bold"))
    for adapter_id, elapsed, counts in per_adapter:
        suffix_bits: list[str] = []
        if counts["errors"]:
            suffix_bits.append(_color(f"errors={counts['errors']}", "red"))
        if counts["person_match"]:
            suffix_bits.append(_color(f"person={counts['person_match']}", "cyan"))
        if counts["breach_hit"]:
            suffix_bits.append(_color(f"breach={counts['breach_hit']}", "yellow"))
        suffix = " " + " ".join(suffix_bits) if suffix_bits else ""
        print(f"  {adapter_id:<32} {elapsed:>6.2f}s  events={counts['events']}{suffix}")
    print()
    print(
        f"Total: {totals['events']} events, "
        f"{totals['person_match']} person-match, "
        f"{totals['breach_hit']} breach-hit, "
        f"{totals['errors']} errors"
    )
    return 0 if totals["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
