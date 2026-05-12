"""Generic live smoke runner for any workflow (W1-W11+).

The W11.em+ smoke (`smoke-w11-em.py`) caught a real bug (user-scanner
async coroutine). This generic version runs the same shape against ANY
workflow defined in `osint_goblin_workers.workflows.WORKFLOWS`.

Usage:
  python tools/dev/smoke-workflow.py <workflow_id> --seed k=v [--seed k=v ...]
  python tools/dev/smoke-workflow.py w9.pv --seed 'address=1600 Pennsylvania Ave, Washington DC'
  python tools/dev/smoke-workflow.py w11.em --seed email=user@example.com
  python tools/dev/smoke-workflow.py w10.ip --seed ip=8.8.8.8

Modes:
  default      live (real network calls to upstreams)
  --synthetic  use each adapter's synthetic_mode (no network)
  --only id    run a single step from the workflow
  --skip id    skip a step from the workflow (comma-separated for multiple)

Skips the Dramatiq + SSE plumbing -- calls each step's adapter in-
process via the registry. This script's job is to verify each step's
wire shape against the real (or synthetic) upstream, not to exercise
the message bus.

Verdict synthesis: at the end of run, applies Margaret's rubric (the
same one in smoke-w11-em.py + apps/web/.../verdict.ts) over the
accumulated events and prints the bucket + confidence + why + next.
The rubric is keyed off event_type + payload.source, so it works for
any workflow whose events follow the standard shape.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

# Allow `python tools/dev/smoke-workflow.py` from repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKER_SRC = os.path.join(_REPO_ROOT, "apps", "workers", "src")
if _WORKER_SRC not in sys.path:
    sys.path.insert(0, _WORKER_SRC)


def _color(s: str, c: str) -> str:
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


def _run_step(
    adapter_id: str, payload: dict[str, Any], synthetic: bool
) -> tuple[float, list[dict[str, Any]]]:
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

    callable_ = entry.synthetic_mode if synthetic else entry.callable
    if callable_ is None:
        mode = "synthetic" if synthetic else "live"
        return (
            0.0,
            [
                {
                    "event_type": "tool-run-error",
                    "payload": {"reason": f"adapter '{adapter_id}' has no {mode} mode"},
                }
            ],
        )

    start = time.monotonic()
    try:
        events = callable_(payload)
    except Exception as exc:
        events = [
            {
                "event_type": "tool-run-error",
                "payload": {"reason": f"adapter raised {type(exc).__name__}: {exc}"},
            }
        ]
    elapsed = time.monotonic() - start
    return (elapsed, events or [])


# Margaret's rubric -- mirror of smoke-w11-em.py. DRY to a shared module
# in a follow-up; duplication is acceptable while there are only two
# call sites.
def _build_signals_from_events(events: list[dict[str, Any]]) -> dict[str, bool]:
    gravatar = github = user_scanner = compromise = False
    for e in events:
        t = e.get("event_type", "")
        payload = e.get("payload", {})
        source = payload.get("source", "") if isinstance(payload, dict) else ""
        if t == "person-match":
            if source == "gravatar":
                gravatar = True
            elif source == "github_commits":
                github = True
            elif source == "user_scanner":
                user_scanner = True
        elif t == "breach-hit":
            compromise = True
    return {
        "identity": gravatar or github,
        "behavior": github,
        "compromise": compromise,
        "consumer_tail": user_scanner,
    }


def _synthesize_verdict(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    interesting = any(
        e.get("event_type") in ("person-match", "breach-hit", "tool-run-result") for e in events
    )
    if not interesting:
        return None
    s = _build_signals_from_events(events)
    if s["identity"] and s["compromise"]:
        return {
            "bucket": "compromised-real",
            "confidence": "high",
            "why": "Owner-attested identity AND infostealer-log compromise",
            "next": "Real person whose machine was infected.",
            "signals": s,
        }
    if s["identity"] and s["behavior"] and not s["compromise"] and not s["consumer_tail"]:
        return {
            "bucket": "real-careful",
            "confidence": "high",
            "why": "Identity + behavior + no compromise + no consumer tail",
            "next": "Real long-lived person, operational discipline.",
            "signals": s,
        }
    if s["identity"] and s["behavior"] and not s["compromise"]:
        return {
            "bucket": "real-active",
            "confidence": "high",
            "why": "Identity + behavior, maybe consumer tail",
            "next": "Typical real-person profile.",
            "signals": s,
        }
    if not s["identity"] and not s["behavior"] and s["compromise"]:
        return {
            "bucket": "suspicious-churn",
            "confidence": "medium",
            "why": "Zero identity + zero behavior + compromise",
            "next": "Likely churn or throwaway. Deep-vet other primitives.",
            "signals": s,
        }
    if not s["identity"] and not s["behavior"] and not s["compromise"] and not s["consumer_tail"]:
        return {
            "bucket": "low-footprint",
            "confidence": "medium",
            "why": "Zero hits across every leg",
            "next": "Email gives no identity bridge; pivot to other primitives.",
            "signals": s,
        }
    return {
        "bucket": "mixed",
        "confidence": "low",
        "why": "Partial signals; pattern doesn't match a clean bucket",
        "next": "Read the per-adapter events directly.",
        "signals": s,
    }


def _parse_seed_args(seed_args: list[str]) -> dict[str, str]:
    """Parse --seed key=value pairs into a dict."""
    seed: dict[str, str] = {}
    for kv in seed_args:
        if "=" not in kv:
            sys.stderr.write(f"warn: ignoring malformed --seed (no '='): {kv}\n")
            continue
        k, _, v = kv.partition("=")
        seed[k.strip()] = v.strip()
    return seed


def main() -> int:
    ap = argparse.ArgumentParser(description="Generic live smoke for any workflow in WORKFLOWS")
    ap.add_argument("workflow_id", help="Workflow id (e.g. w9.pv, w11.em, w10.ip)")
    ap.add_argument(
        "--seed",
        action="append",
        default=[],
        help="Seed payload entry: key=value (repeatable)",
    )
    ap.add_argument(
        "--synthetic",
        action="store_true",
        help="Use each adapter's synthetic_mode (no network)",
    )
    ap.add_argument(
        "--only",
        default="",
        help="Comma-separated adapter ids to include (default: all workflow steps)",
    )
    ap.add_argument(
        "--skip",
        default="",
        help="Comma-separated adapter ids to skip",
    )
    args = ap.parse_args()

    from osint_goblin_workers.workflows import get_workflow

    workflow = get_workflow(args.workflow_id)
    if workflow is None:
        print(_color(f"ERR: workflow '{args.workflow_id}' not found", "red"))
        return 2

    seed = _parse_seed_args(args.seed)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    print(_color(f"workflow {workflow.id}: {workflow.name}", "bold"))
    print(_color(f"  {workflow.description}", "grey"))
    print(_color(f"  seed: {json.dumps(seed)}", "grey"))
    if args.synthetic:
        print(_color("  mode: synthetic (no network)", "yellow"))
    print()

    totals = {"events": 0, "errors": 0, "person_match": 0, "breach_hit": 0}
    per_step: list[tuple[str, float, dict[str, int]]] = []
    all_events: list[dict[str, Any]] = []
    # Mirror workflow_runner: accumulate each step's events so subsequent
    # steps can resolve `inputs_from` against prior outputs.
    from osint_goblin_workers.workflows import resolve_inputs_from

    step_results: list[list[dict[str, Any]]] = []

    for step in workflow.steps:
        if only and step.adapter_id not in only:
            step_results.append([])
            continue
        if step.adapter_id in skip:
            step_results.append([])
            continue
        payload = step.build_payload(seed)
        if payload is None:
            missing = step.required_seed_keys
            print(
                _color(
                    f"-- {step.adapter_id}  SKIPPED (required seed keys not present: {missing})",
                    "yellow",
                )
            )
            print()
            step_results.append([])
            continue

        # Apply inputs_from overrides (Margaret ship #2). Resolves
        # step{N}.payload.{key} references against prior step events.
        if step.inputs_from:
            overrides = resolve_inputs_from(step.inputs_from, step_results)
            if overrides:
                payload = {**payload, **overrides}

        print(_color(f"-- {step.adapter_id}", "bold"))
        if step.description:
            print(_color(f"   {step.description}", "grey"))
        elapsed, events = _run_step(step.adapter_id, payload, args.synthetic)
        step_results.append(events)
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
        per_step.append((step.adapter_id, elapsed, counts))
        all_events.extend(events)
        print(_color(f"  ({len(events)} events in {elapsed:.2f}s)", "grey"))
        print()

    print(_color("Summary", "bold"))
    for adapter_id, elapsed, counts in per_step:
        bits: list[str] = []
        if counts["errors"]:
            bits.append(_color(f"errors={counts['errors']}", "red"))
        if counts["person_match"]:
            bits.append(_color(f"person={counts['person_match']}", "cyan"))
        if counts["breach_hit"]:
            bits.append(_color(f"breach={counts['breach_hit']}", "yellow"))
        suffix = " " + " ".join(bits) if bits else ""
        print(f"  {adapter_id:<32} {elapsed:>6.2f}s  events={counts['events']}{suffix}")
    print()
    print(
        f"Total: {totals['events']} events, "
        f"{totals['person_match']} person-match, "
        f"{totals['breach_hit']} breach-hit, "
        f"{totals['errors']} errors"
    )

    verdict = _synthesize_verdict(all_events)
    if verdict:
        bucket_color = {
            "real-careful": "green",
            "real-active": "green",
            "compromised-real": "yellow",
            "suspicious-churn": "red",
            "low-footprint": "yellow",
            "mixed": "blue",
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
