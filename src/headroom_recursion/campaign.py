"""Campaign mode — a goal held open across many runs, honestly.

A campaign repeatedly runs the same (research-wrapped) problem, seeding each
run's scratchpad from the ledger so progress is monotone: settled ground is
carried, never re-derived (or worse, re-fabricated). Every run persists its
full trace; a rolling ``campaign-summary.json`` records the trajectory; a
heartbeat file pulses per model call.

A campaign stops for exactly one of five reasons, recorded in the summary:

* ``validated``  — a run halted on mechanical verification (the goal state),
* ``dry``        — the ledger failed to improve ``dry_stop`` runs in a row,
* ``cost-cap``   — the dollar fuse blew (mid-run the cap aborts remaining
                   tiers via ``CostCapExceeded``; the run keeps its best answer),
* ``exhausted``  — all ``runs`` ran without any of the above,
* ``error``      — an unexpected non-run error (individual run deaths are
                   survived: their partial traces persist and the loop continues).

The stop rule is defined by ledger movement, so the ledger's judged tier must
not ratchet on judge noise — ``ledger.record`` requires a judged entry to beat
the incumbent by a real margin (see ``ledger.JUDGED_EPSILON``).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from headroom_recursion import ledger as ledger_mod
from headroom_recursion.config import RecurseConfig
from headroom_recursion.heartbeat import HeartbeatClient
from headroom_recursion.ladder import RunError, recurse


@dataclass
class CampaignResult:
    stopped: str                 # validated | dry | cost-cap | exhausted
    runs_completed: int
    total_calls: int
    total_cost_usd: float
    best_halt_prob: float
    validated: bool
    summary_path: str
    run_stems: list[str] = field(default_factory=list)


def _log(msg: str) -> None:
    print(f"campaign: {msg}", file=sys.stderr)


def run_campaign(
    problem: str,
    *,
    client,
    base_config: RecurseConfig,
    runs: int = 10,
    ledger_path: str,
    trace_dir: str = "runs",
    heartbeat_path: Optional[str] = None,
    dry_stop: int = 2,
    max_cost_usd: Optional[float] = None,
    log: Callable[[str], None] = _log,
) -> CampaignResult:
    """Run up to ``runs`` ledger-seeded runs of ``problem``; see module doc."""

    if runs < 1:
        raise ValueError(f"runs must be >= 1 (got {runs})")
    if dry_stop < 1:
        raise ValueError(f"dry_stop must be >= 1 (got {dry_stop})")

    hb = HeartbeatClient(
        client,
        heartbeat_path or os.path.join(trace_dir, "heartbeat.json"),
        max_cost_usd=max_cost_usd,
    )
    os.makedirs(trace_dir, exist_ok=True)
    key = ledger_mod.problem_key(problem)

    # R8 guard: the ledger keys the WRAPPED problem text. A ledger that has
    # entries but none for this problem usually means the research template
    # changed mid-campaign — progress would silently restart from zero.
    existing = ledger_mod.load(ledger_path)
    if existing and key not in existing:
        log(
            f"WARNING: ledger {ledger_path} has {len(existing)} entr(y/ies) but none for "
            f"this problem (key {key}) — was the research template edited? "
            "Seeding starts from scratch."
        )

    summary: dict = {
        "problem_key": key,
        "problem_preview": problem.strip()[:300],
        "dry_stop": dry_stop,
        "max_cost_usd": max_cost_usd,
        "runs": [],
    }
    summary_path = os.path.join(trace_dir, "campaign-summary.json")

    stopped = "exhausted"
    best = -1.0
    validated = False
    stems: list[str] = []
    dry = 0

    for i in range(runs):
        hb.meta.update(run=i, of=runs, dry=dry)
        pad_seed, ans_seed = ledger_mod.seed_pair(ledger_path, problem)
        cfg = replace(base_config, seed_scratchpad=pad_seed, seed_answer=ans_seed)
        log(f"run {i}: starting (seeded={'yes' if ans_seed else 'no'}, "
            f"spent ${hb.cost_usd:.2f})")
        try:
            trace = recurse(problem, client=hb, config=cfg)
        except RunError as exc:
            # One dead run must not kill the campaign; its evidence persists.
            trace = exc.trace
            log(f"run {i}: died mid-flight ({exc}); partial trace kept, continuing")

        stem = f"run-{i:03d}"
        stems.append(stem)
        try:
            trace.persist(trace_dir, stem=stem)
        except OSError as exc:
            log(f"run {i}: could not persist trace: {exc}")

        improved = bool(trace.final_answer) and ledger_mod.record(ledger_path, problem, trace)
        dry = 0 if improved else dry + 1
        best = max(best, trace.best_halt_prob)
        validated = validated or trace.stop_reason == "validated"

        summary["runs"].append(
            {
                "run": i,
                "stem": stem,
                "stop_reason": trace.stop_reason,
                "halted": trace.halted,
                "best_halt_prob": trace.best_halt_prob,
                "ledger_improved": improved,
                "calls": trace.total_calls,
                "cost_usd_cumulative": round(hb.cost_usd, 4),
                "needs_human_review": trace.needs_human_review,
            }
        )
        _write_summary(summary_path, summary)
        log(
            f"run {i}: {trace.stop_reason} (best {trace.best_halt_prob:.2f}, "
            f"ledger {'improved' if improved else 'unchanged'}, "
            f"cumulative ${hb.cost_usd:.2f})"
        )

        if validated:
            stopped = "validated"
            break
        if dry >= dry_stop:
            stopped = "dry"
            log(f"ledger dry {dry} consecutive run(s) — stopping")
            break
        if max_cost_usd is not None and hb.cost_usd >= max_cost_usd:
            stopped = "cost-cap"
            log(f"cost cap ${max_cost_usd:.2f} reached — stopping")
            break

    summary["stopped"] = stopped
    summary["total_cost_usd"] = round(hb.cost_usd, 4)
    summary["total_calls"] = hb.calls
    _write_summary(summary_path, summary)
    hb.meta.update(stopped=stopped)
    hb.beat(status="campaign-finished")

    return CampaignResult(
        stopped=stopped,
        runs_completed=len(stems),
        total_calls=hb.calls,
        total_cost_usd=round(hb.cost_usd, 4),
        best_halt_prob=best,
        validated=validated,
        summary_path=summary_path,
        run_stems=stems,
    )


def _write_summary(path: str, summary: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    os.replace(tmp, path)


def campaign_report(trace_dir: str, ledger_path: str) -> str:
    """Assemble the submittable markdown: what is verified, what is judged,
    what needs a human — from artifacts alone."""

    summary_path = os.path.join(trace_dir, "campaign-summary.json")
    try:
        with open(summary_path, "r", encoding="utf-8") as fh:
            summary = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return "# Campaign report\n\nNo campaign summary found — nothing to report.\n"

    entry = ledger_mod.load(ledger_path).get(summary.get("problem_key", ""), {})
    lines = [
        "# Campaign report",
        "",
        f"- problem key: `{summary.get('problem_key')}`",
        f"- stopped: **{summary.get('stopped', '?')}** after "
        f"{len(summary.get('runs', []))} run(s), "
        f"{summary.get('total_calls', '?')} model calls, "
        f"${summary.get('total_cost_usd', 0):.2f}",
        "",
        "## Authority of the outcome",
        "",
    ]
    if entry.get("verified"):
        lines += [
            "The ledger's best entry is **mechanically verified** "
            f"(stop reason: {entry.get('stop_reason')}). Its verification artifacts "
            "(trace JSON; for rung-1, the spliced Lean file + compiler output + axiom "
            f"audit) are under `{trace_dir}/`.",
        ]
    elif entry:
        lines += [
            "The ledger's best entry rests on **judged opinion** "
            f"(best halt_prob {entry.get('best_halt_prob', 0):.2f})"
            + (" and is flagged **NEEDS HUMAN REVIEW**." if entry.get("needs_human_review")
               else "."),
            "",
            "Nothing here is a verified result; treat the answer as a draft whose "
            "grading rubric and per-step judge scores are in the run traces.",
        ]
    else:
        lines += ["No ledger entry was produced — no claim of any kind survived."]

    lines += ["", "## Run trajectory", "", "| run | stop | best | ledger | calls | $cum |", "|---|---|---|---|---|---|"]
    for r in summary.get("runs", []):
        lines.append(
            f"| {r['run']} | {r['stop_reason']} | {r['best_halt_prob']:.2f} | "
            f"{'+' if r['ledger_improved'] else '='} | {r['calls']} | "
            f"{r['cost_usd_cumulative']:.2f} |"
        )
    lines += [
        "",
        "## Provenance",
        "",
        f"- per-run traces: `{trace_dir}/run-*.json` (+ `.summary.txt`)",
        f"- ledger: `{ledger_path}` (verified-beats-judged, judged entries must beat "
        "the incumbent by ≥ 0.05)",
        f"- lean decider artifacts (if any): `{trace_dir}/lean/`",
        "",
        "Scores are judged opinion unless explicitly marked verified; the rubric caps "
        "fabricated arguments at 0.05 and self-assessment carries zero weight.",
        "",
    ]
    return "\n".join(lines)
