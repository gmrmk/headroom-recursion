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


# Margaret's free-stack rubric (2026-05-11 persona deliberation).
# Maps signal patterns to investigator-actionable verdict buckets. Each
# bucket carries a one-line `why` (which signals fired) and a one-line
# `next` (what the investigator should pivot to). Confidence is a hint
# about how distinctive the pattern is, not a probability.
_VERDICT_RULES: tuple[dict[str, Any], ...] = (
    {
        "bucket": "compromised-real",
        "test": lambda s: s["identity"] and s["compromise"],
        "confidence": "high",
        "why": "Owner-attested identity AND infostealer-log compromise",
        "next": (
            "Real person whose machine was infected. Trust identity claims; "
            "flag compromise context in dossier."
        ),
    },
    {
        "bucket": "real-careful",
        "test": lambda s: s["identity"]
        and s["behavior"]
        and not s["compromise"]
        and not s["consumer_tail"],
        "confidence": "high",
        "why": (
            "Owner-attested identity + behavioral confirmation + "
            "no compromise + no consumer-service tail"
        ),
        "next": "Real long-lived person with operational discipline. Identity reliably anchored.",
    },
    {
        "bucket": "real-active",
        "test": lambda s: s["identity"] and s["behavior"] and not s["compromise"],
        "confidence": "high",
        "why": "Identity + behavior, possibly some consumer-service tail",
        "next": (
            "Typical real-person profile. Identity anchored; "
            "cross-check claims via Gravatar/GitHub URLs."
        ),
    },
    {
        "bucket": "suspicious-churn",
        "test": lambda s: not s["identity"] and not s["behavior"] and s["compromise"],
        "confidence": "medium",
        "why": "Zero identity + zero behavior + compromise hits",
        "next": (
            "Likely churn or throwaway account. Flag as anomaly; "
            "deep-vet listing photos + address records."
        ),
    },
    {
        "bucket": "low-footprint",
        "test": lambda s: not s["identity"]
        and not s["behavior"]
        and not s["compromise"]
        and not s["consumer_tail"],
        "confidence": "medium",
        "why": "Zero hits across every leg (identity, behavior, compromise, consumer)",
        "next": (
            "Email gives no identity bridge. Pivot to address records, "
            "listing photos, phone, host display name."
        ),
    },
    {
        "bucket": "mixed",
        "test": lambda _s: True,
        "confidence": "low",
        "why": "Partial signals; pattern doesn't match a clean bucket",
        "next": "Read the per-adapter events directly; rubric uncertain.",
    },
)


def _build_signals(per_adapter: list[tuple[str, float, dict[str, int]]]) -> dict[str, bool]:
    """Reduce per-adapter counts to four boolean signals Margaret's rubric
    operates on. Counts are derived from event_type rollups, not raw
    upstream fields, so the synthesis is stable across schema drift."""
    by_id = {aid: counts for aid, _elapsed, counts in per_adapter}
    gravatar = by_id.get("gravatar_profile_lookup", {}).get("person_match", 0) > 0
    github = by_id.get("github_commit_email_search", {}).get("person_match", 0) > 0
    user_scanner_hits = by_id.get("user_scanner", {}).get("person_match", 0) > 0
    hudson_rock = by_id.get("hudson_rock_email_check", {}).get("breach_hit", 0) > 0
    hibp = by_id.get("hibp_breach_check", {}).get("breach_hit", 0) > 0
    return {
        "identity": gravatar or github,
        "behavior": github,
        "compromise": hudson_rock or hibp,
        "consumer_tail": user_scanner_hits,
    }


def _synthesize_verdict(
    per_adapter: list[tuple[str, float, dict[str, int]]],
) -> dict[str, Any]:
    """Apply Margaret's rubric to per-adapter counts and return a verdict
    dict with bucket / confidence / why / next."""
    signals = _build_signals(per_adapter)
    for rule in _VERDICT_RULES:
        if rule["test"](signals):
            return {
                "bucket": rule["bucket"],
                "confidence": rule["confidence"],
                "why": rule["why"],
                "next": rule["next"],
                "signals": signals,
            }
    # Defensive -- the final rule always matches.
    return {
        "bucket": "unknown",
        "confidence": "low",
        "why": "no rule matched",
        "next": "manual review",
        "signals": signals,
    }


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

    # Margaret's verdict synthesis -- one-line investigator-actionable read.
    verdict = _synthesize_verdict(per_adapter)
    bucket_color = {
        "real-careful": "green",
        "real-active": "green",
        "compromised-real": "yellow",
        "suspicious-churn": "red",
        "low-footprint": "yellow",
        "mixed": "blue",
        "unknown": "grey",
    }.get(verdict["bucket"], "blue")
    print()
    print(
        _color("Verdict: ", "bold")
        + _color(verdict["bucket"], bucket_color)
        + _color(f"  ({verdict['confidence']} confidence)", "grey")
    )
    print(_color(f"  why : {verdict['why']}", "grey"))
    print(_color(f"  next: {verdict['next']}", "grey"))
    return 0 if totals["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
