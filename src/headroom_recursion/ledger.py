"""The run ledger — monotone progress across runs.

A JSON file mapping problem keys to the best outcome any run has produced for
that problem. Verified (mechanically validated) entries are seeded into later
runs' scratchpads as trusted ground; judged-only entries are seeded with an
explicit caveat. The point: never re-derive — and never re-fabricate — ground a
previous run already settled.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional


def problem_key(problem: str) -> str:
    return hashlib.sha256(problem.strip().encode()).hexdigest()[:16]


def load(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def seed_for(path: str, problem: str) -> str:
    """Scratchpad seed for ``problem`` from the ledger, or '' when none exists."""

    entry = load(path).get(problem_key(problem))
    if not entry or not entry.get("answer"):
        return ""
    if entry.get("verified"):
        header = "[LEDGER — VERIFIED] A previous run mechanically validated this result:"
    else:
        header = (
            "[LEDGER — JUDGED, NOT VERIFIED] A previous run's best judged attempt "
            f"(halt_prob {entry.get('best_halt_prob', 0):.2f}); treat as a draft, not ground truth:"
        )
    return f"{header}\n{entry['answer']}"


# A judged (non-verified) entry must beat the incumbent by this margin to be
# recorded. Judged scores are noisy; a zero-margin ratchet "improves" forever
# on judge variance alone and defeats any dry-stop rule built on ledger
# movement. Verified entries carry no margin — mechanical verdicts don't drift.
JUDGED_EPSILON = 0.05


def record(path: str, problem: str, trace) -> bool:
    """Persist the run's outcome if it beats what the ledger already holds.

    Verified beats judged; a verified score must merely exceed the incumbent,
    a judged score must exceed it by ``JUDGED_EPSILON``. Returns True when the
    ledger was updated.
    """

    verified = trace.stop_reason == "validated"
    score = float(getattr(trace, "best_halt_prob", 0.0))
    answer = trace.final_answer
    if not answer:
        return False

    data = load(path)
    key = problem_key(problem)
    old = data.get(key)
    if old is not None:
        if old.get("verified") and not verified:
            return False
        if old.get("verified") == verified:
            margin = 0.0 if verified else JUDGED_EPSILON
            if score <= float(old.get("best_halt_prob", 0)) + margin:
                return False

    data[key] = {
        "problem": problem.strip()[:300],
        "answer": answer,
        "verified": verified,
        "stop_reason": trace.stop_reason,
        "best_halt_prob": score,
        "needs_human_review": bool(getattr(trace, "needs_human_review", False)),
        "settles_at": getattr(trace, "settles_at", "") or "",
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)
    return True
