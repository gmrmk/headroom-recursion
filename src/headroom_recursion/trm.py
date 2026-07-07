"""The core recursive-reasoning loop for a single model tier.

Mirrors TRM's ``deep_recursion``: each *improvement step* runs ``n`` latent updates
(refine the scratchpad ``z``) followed by one answer update (rewrite ``y`` from
``z``), carrying ``(y, z)`` forward between steps. After each step the halt predictor
decides whether to stop.

A tier stops for one of four reasons:
* ``validated``  — the optional oracle confirms the answer (halts the whole run),
* ``halt``       — the judge's halt_prob crosses the threshold (halts the whole run),
* ``converged``  — the answer is stable, so this tier has nothing left to add (escalate),
* ``exhausted``  — the tier's step budget ran out (escalate).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from headroom_recursion import halting, prompts
from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.trace import RunTrace, StepTrace


@dataclass
class TierResult:
    answer: str
    scratchpad: str
    halted: bool
    stop_reason: str  # "validated" | "halt" | "converged" | "exhausted"


def run_tier(
    client,
    cfg: RecurseConfig,
    tier: Tier,
    problem: str,
    answer: str,
    scratchpad: str,
    trace: RunTrace,
) -> TierResult:
    """Run improvement steps at one tier, carrying ``(answer, scratchpad)`` forward."""

    steps = cfg.steps_for(tier)
    judge_model = cfg.judge_model or tier.model
    stop_reason = "exhausted"

    for _ in range(steps):
        prev_answer = answer
        tb = ta = 0
        latent_calls = 0

        # --- n latent updates:  z = net(x, y, z) ---
        for _ in range(cfg.n):
            res = client.complete(
                model=tier.model,
                system=prompts.LATENT_SYSTEM,
                user=prompts.LATENT_UPDATE.format(
                    problem=problem, answer=answer or "(none yet)", scratchpad=scratchpad or "(empty)"
                ),
                max_tokens=tier.max_tokens,
                temperature=cfg.temperature,
                use_headroom=cfg.use_headroom,
            )
            scratchpad = res.text
            latent_calls += 1
            tb += res.tokens_before
            ta += res.tokens_after

        # --- 1 answer update:  y = net(y, z) ---
        res = client.complete(
            model=tier.model,
            system=prompts.ANSWER_SYSTEM,
            user=prompts.ANSWER_UPDATE.format(problem=problem, scratchpad=scratchpad, answer=answer or "(none yet)"),
            max_tokens=tier.max_tokens,
            temperature=cfg.temperature,
            use_headroom=cfg.use_headroom,
        )
        answer = res.text
        tb += res.tokens_before
        ta += res.tokens_after

        converged = _same(answer, prev_answer) and prev_answer != ""

        # --- oracle short-circuit ---
        validated = bool(cfg.validator and _safe_validate(cfg.validator, answer))

        # --- halt predictor (Q-head) ---
        if validated:
            halt_prob, reason = 1.0, "validator confirmed"
        else:
            verdict = halting.judge(
                client,
                model=judge_model,
                problem=problem,
                answer=answer,
                scratchpad=scratchpad,
                max_tokens=min(256, tier.max_tokens),
                use_headroom=cfg.use_headroom,
            )
            halt_prob, reason = verdict.halt_prob, verdict.reason
            tb += verdict.tokens_before
            ta += verdict.tokens_after

        halted = validated or halt_prob >= cfg.halt_threshold
        stop_reason = "validated" if validated else ("halt" if halted else ("converged" if converged else "exhausted"))

        trace.add(
            StepTrace(
                tier_model=tier.model,
                step_index=len(trace.steps),
                latent_calls=latent_calls,
                answer_preview=_preview(answer),
                halt_prob=halt_prob,
                halted=halted,
                converged=converged,
                reason=reason,
                tokens_before=tb,
                tokens_after=ta,
            )
        )

        if halted:
            return TierResult(answer, scratchpad, True, stop_reason)
        if converged:
            # This tier has stopped moving — escalate rather than spin.
            return TierResult(answer, scratchpad, False, "converged")

    return TierResult(answer, scratchpad, False, stop_reason)


def _same(a: str, b: str) -> bool:
    return _norm(a) == _norm(b)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _preview(s: str, n: int = 160) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _safe_validate(validator, answer: str) -> bool:
    try:
        return bool(validator(answer))
    except Exception:
        return False
