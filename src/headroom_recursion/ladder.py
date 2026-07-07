"""Tier escalation — the "less is more" controller.

Start recursing on the cheapest model. If it halts (a confident, verified answer),
we are done and never pay for a bigger model. If it plateaus — converges on a stable
answer or exhausts its step budget without a confident halt — carry the current
``(answer, scratchpad)`` up to the next tier, which continues refining from that best
draft rather than restarting. This is the LLM echo of the paper's finding that a tiny
recursive network can match models thousands of times its size.

Failure containment: a run NEVER loses its work. Ctrl-C returns the partial trace
(``stop_reason="interrupted"``); an API error raises ``RunError`` that still carries
the partial trace; a blown budget stops the run where it stands — it never escalates
to a more expensive tier after the budget is already gone.
"""

from __future__ import annotations

import time
from typing import Optional

from headroom_recursion import trm
from headroom_recursion.config import RecurseConfig
from headroom_recursion.trace import RunTrace


class RunError(RuntimeError):
    """A run died mid-flight. ``.trace`` holds everything completed before the error."""

    def __init__(self, cause: BaseException, trace: RunTrace):
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.trace = trace


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
    cfg.validate()
    trace = RunTrace(problem=problem)

    start = time.monotonic()
    deadline = start + cfg.max_wall_seconds if cfg.max_wall_seconds is not None else None

    answer = ""
    scratchpad = ""
    stop_reason = "no-op"
    final_model = ""

    def finalize(*, halted: bool, reason: str) -> RunTrace:
        trace.halted = halted
        trace.stop_reason = reason
        trace.final_model = final_model
        if halted:
            trace.final_answer = answer
        else:
            # Abnormal / non-confident exit: prefer the best-scoring answer seen.
            trace.final_answer = trace.best_answer if trace.best_step_index >= 0 else answer
        return trace

    try:
        for tier in cfg.ladder:
            final_model = tier.model
            result = trm.run_tier(
                client, cfg, tier, problem, answer, scratchpad, trace, deadline=deadline
            )
            answer, scratchpad = result.answer, result.scratchpad
            trace.tier_stops.append(f"{tier.model}: {result.stop_reason}")
            stop_reason = result.stop_reason
            if result.halted:
                return finalize(halted=True, reason=stop_reason)
            if result.stop_reason == "budget":
                # The budget is spent — escalating to a pricier tier now is exactly
                # the runaway behavior budgets exist to prevent.
                break
        return finalize(halted=False, reason=stop_reason)
    except KeyboardInterrupt:
        trace.error = "KeyboardInterrupt"
        return finalize(halted=False, reason="interrupted")
    except Exception as exc:
        trace.error = f"{type(exc).__name__}: {exc}"
        finalize(halted=False, reason="error")
        raise RunError(exc, trace) from exc
    finally:
        trace.wall_seconds = time.monotonic() - start


def plan_schedule(cfg: Optional[RecurseConfig] = None) -> str:
    """Describe the maximum call schedule without hitting the API (for --dry-run)."""

    cfg = cfg or RecurseConfig()
    lines = ["Planned recursion schedule (worst case, before early halting):", ""]
    grand = 0
    per_step = cfg.n + 1 + cfg.judge_votes  # n latent + 1 answer + judge votes
    for i, tier in enumerate(cfg.ladder):
        steps = cfg.steps_for(tier)
        tier_calls = steps * per_step
        grand += tier_calls
        judge = cfg.judge_model or tier.model
        judge_label = f"{cfg.judge_votes} judge" if cfg.judge_votes > 1 else "1 judge"
        lines.append(
            f"  tier {i}: {tier.model}\n"
            f"           {steps} steps x ({cfg.n} latent + 1 answer + {judge_label}) = {tier_calls} calls"
            + (f"  [judge: {judge}]" if cfg.judge_model else "")
        )
    lines += [
        "",
        f"  escalation: cheapest -> most capable; stops at first confident halt",
        f"  halt_threshold: {cfg.halt_threshold}   headroom: {'on' if cfg.use_headroom else 'off'}",
    ]
    if cfg.judge_model is None:
        lines.append(
            "  judge: runs on the working tier (self-preference risk); pin --judge-model "
            "to a different model to reduce it"
        )
    if cfg.max_total_calls is not None:
        lines.append(f"  budget: max {cfg.max_total_calls} Claude calls (stops, never escalates past it)")
    if cfg.max_wall_seconds is not None:
        lines.append(f"  budget: max {cfg.max_wall_seconds:.0f}s wall clock")
    lines.append(f"  worst-case total Claude calls: {grand}")
    return "\n".join(lines)
