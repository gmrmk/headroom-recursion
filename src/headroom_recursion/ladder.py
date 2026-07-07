"""Tier escalation — the "less is more" controller.

Start recursing on the cheapest model. If it halts (a confident, verified answer),
we are done and never pay for a bigger model. If it plateaus — converges on a stable
answer or exhausts its step budget without a confident halt — carry the current
``(answer, scratchpad)`` up to the next tier, which continues refining from that best
draft rather than restarting. This is the LLM echo of the paper's finding that a tiny
recursive network can match models thousands of times its size.
"""

from __future__ import annotations

from typing import Optional

from headroom_recursion import trm
from headroom_recursion.config import RecurseConfig
from headroom_recursion.trace import RunTrace


def recurse(
    problem: str,
    *,
    client,
    config: Optional[RecurseConfig] = None,
) -> RunTrace:
    """Run the full draft -> recurse -> escalate loop and return the trace.

    ``client`` is any object exposing ``ClaudeClient.complete``'s signature, so tests
    can pass a stub with no network.
    """

    cfg = config or RecurseConfig()
    trace = RunTrace(problem=problem)

    answer = ""
    scratchpad = ""

    for tier in cfg.ladder:
        result = trm.run_tier(client, cfg, tier, problem, answer, scratchpad, trace)
        answer, scratchpad = result.answer, result.scratchpad
        if result.halted:
            trace.final_answer = answer
            trace.halted = True
            trace.stop_reason = result.stop_reason
            trace.final_model = tier.model
            return trace

    # Every tier exhausted without a confident halt: return the best draft we have.
    trace.final_answer = answer
    trace.halted = False
    trace.stop_reason = result.stop_reason if cfg.ladder else "no-op"
    trace.final_model = cfg.ladder[-1].model if cfg.ladder else ""
    return trace


def plan_schedule(cfg: Optional[RecurseConfig] = None) -> str:
    """Describe the maximum call schedule without hitting the API (for --dry-run)."""

    cfg = cfg or RecurseConfig()
    lines = ["Planned recursion schedule (worst case, before early halting):", ""]
    grand = 0
    for i, tier in enumerate(cfg.ladder):
        steps = cfg.steps_for(tier)
        per_step = cfg.n + 2  # n latent + 1 answer + 1 judge
        tier_calls = steps * per_step
        grand += tier_calls
        judge = cfg.judge_model or tier.model
        lines.append(
            f"  tier {i}: {tier.model}\n"
            f"           {steps} steps x ({cfg.n} latent + 1 answer + 1 judge) = {tier_calls} calls"
            + (f"  [judge: {judge}]" if cfg.judge_model else "")
        )
    lines += [
        "",
        f"  escalation: cheapest -> most capable; stops at first confident halt",
        f"  halt_threshold: {cfg.halt_threshold}   headroom: {'on' if cfg.use_headroom else 'off'}",
        f"  worst-case total Claude calls: {grand}",
    ]
    return "\n".join(lines)
