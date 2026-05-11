"""user-scanner subprocess wrapper (ship #4 of Margaret's free-stack).

Runs in the empirical venv where user-scanner is `pip install`'d. The
osint-goblin worker dispatches this via subprocess_adapter with
python_executable pinned to the empirical venv path.

user-scanner (github.com/kaifcodec/user-scanner) is the active successor
to holehe (megadose, last touched Sep 2024 and degraded). It probes 95+
services from a single email via password-reset / signup-existence
heuristics. Pure-httpx, no Playwright. Output: one Result per probed
service with `is_found` / `available` / `error` / `skipped` markers.

Contract (Sora ADR-0004 sec.5):
  stdin:  {"email": "...", "category": "..."} on one line, EOF
            -- category optional; absent = scan all categories
  stdout: NDJSON event objects, one per line
  stderr: free-form log
  exit:   0 on clean run; non-zero on adapter failure

Wire shape:
  - one `person-match` per Result with is_found=True (cap 50)
  - one `tool-run-result` summary with checked/found/errored counts

Synthetic mode (OSINT_ADAPTER_MODE=synthetic): bypasses user-scanner
entirely and emits a deterministic fixture; used when user-scanner is
not installed in the empirical venv yet.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import UTC, datetime


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_synthetic(payload: dict) -> int:
    """Synthetic event stream -- no network, deterministic. Used when
    user-scanner is not installed or OSINT_ADAPTER_MODE=synthetic."""
    email = payload.get("email", "user@example.com")
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {"adapter": "user_scanner", "email": email, "synthetic": True},
        }
    )
    samples = [
        {"site": "github", "url": "https://github.com", "category": "Development"},
        {"site": "spotify", "url": "https://spotify.com", "category": "Music"},
        {"site": "duolingo", "url": "https://duolingo.com", "category": "Learning"},
    ]
    for s in samples:
        _emit(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "user_scanner",
                    "email": email,
                    "platform": s["site"],
                    "category": s["category"],
                    "profile_url": s["url"],
                    "synthetic": True,
                },
            }
        )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "user_scanner",
                "email": email,
                "checked": 95,
                "found": len(samples),
                "errored": 0,
                "synthetic": True,
            },
        }
    )
    return 0


def _run_live(payload: dict) -> int:
    """Live path: import user_scanner.core.engine and call check_all.
    Each Result with is_found=True becomes one person-match event."""
    email = payload.get("email", "")
    if not isinstance(email, str) or "@" not in email:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing or malformed 'email'"},
            }
        )
        return 1
    try:
        from user_scanner.core import engine  # noqa: PLC0415
    except ImportError as exc:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": (
                        f"user-scanner not installed in this interpreter: {exc}. "
                        "Install via `pip install user-scanner` in the empirical venv."
                    ),
                    "email": email,
                },
            }
        )
        return 1

    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {"adapter": "user_scanner", "email": email, "started": _now_iso()},
        }
    )

    try:
        results = engine.check_all(email, is_email=True)
    except Exception as exc:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"user-scanner check_all failed: {type(exc).__name__}: {exc}",
                    "email": email,
                    "traceback_tail": traceback.format_exc()[-800:],
                },
            }
        )
        return 1

    checked = 0
    found = 0
    errored = 0
    # Dossier noise cap: 50 platforms is plenty; the rest stay in totals.
    person_match_budget = 50
    for r in results:
        checked += 1
        d: dict
        try:
            d = r.to_dict()
        except Exception:
            d = {}
        if d.get("error"):
            errored += 1
            continue
        if not d.get("is_found", False):
            continue
        found += 1
        if person_match_budget <= 0:
            continue
        person_match_budget -= 1
        _emit(
            {
                "event_type": "person-match",
                "payload": {
                    "source": "user_scanner",
                    "email": email,
                    "platform": d.get("site_name", "") or d.get("module_name", ""),
                    "category": d.get("category", ""),
                    "profile_url": d.get("url", ""),
                    "extra": d.get("extra", ""),
                },
            }
        )

    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": "user_scanner",
                "email": email,
                "checked": checked,
                "found": found,
                "errored": errored,
                "finished": _now_iso(),
            },
        }
    )
    return 0


def _main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        _emit({"event_type": "tool-run-error", "payload": {"reason": "empty stdin"}})
        return 1
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        _emit(
            {"event_type": "tool-run-error", "payload": {"reason": f"bad stdin JSON: {exc}"}}
        )
        return 1

    if os.environ.get("OSINT_ADAPTER_MODE", "").lower() == "synthetic":
        return _run_synthetic(payload)
    return _run_live(payload)


if __name__ == "__main__":
    sys.exit(_main())
