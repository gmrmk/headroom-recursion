"""The core recursive-reasoning loop for a single model tier.

Mirrors TRM's ``deep_recursion``: each *improvement step* runs ``n`` latent updates
(refine the scratchpad ``z``) followed by one answer update (rewrite ``y`` from
``z``), carrying ``(y, z)`` forward between steps. After each step the halt predictor
decides whether to stop.

A tier stops for one of five reasons:
* ``validated``  — the optional oracle confirms the answer (halts the whole run),
* ``halt``       — the judge's halt_prob crosses the threshold (halts the whole run),
* ``converged``  — the answer repeats an earlier one; nothing left here (escalate),
* ``exhausted``  — the tier's step budget ran out (escalate),
* ``budget``     — a hard run budget (calls / wall clock) was hit (stop, NO escalation).

Self-protection rules baked into the loop:
* Empty/whitespace completions never replace the scratchpad or answer — one bad
  completion must not wipe the loop's memory (counted in ``rejected_updates``).
* Convergence checks against ALL answers seen in the tier, so an A→B→A oscillation
  is caught, not just an exact repeat of the previous step.
* Every candidate answer is scored into ``trace.note_candidate`` so abnormal exits
  can return the best-scoring answer, not merely the latest one.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

from headroom_recursion import halting, prompts
from headroom_recursion.config import RecurseConfig, Tier
from headroom_recursion.trace import RunTrace, StepTrace


@dataclass
class TierResult:
    answer: str
    scratchpad: str
    halted: bool
    stop_reason: str  # "validated" | "halt" | "converged" | "exhausted" | "budget"


def run_tier(
    client,
    cfg: RecurseConfig,
    tier: Tier,
    problem: str,
    answer: str,
    scratchpad: str,
    trace: RunTrace,
    deadline: Optional[float] = None,
) -> TierResult:
    """Run improvement steps at one tier, carrying ``(answer, scratchpad)`` forward."""

    steps = cfg.steps_for(tier)
    judge_model = cfg.judge_model or tier.model
    judge_headroom = cfg.use_headroom and cfg.compress_judge
    stop_reason = "exhausted"
    # All answers this tier has seen (seeded with the carried-in draft): repeating any
    # of them means the tier is cycling and should escalate, not spin.
    seen: set[str] = {_norm(answer)} if answer else set()

    for _ in range(steps):
        # --- hard budgets: checked at step boundaries, overshoot <= one step ---
        if cfg.max_total_calls is not None and trace.total_calls >= cfg.max_total_calls:
            return TierResult(answer, scratchpad, False, "budget")
        if deadline is not None and time.monotonic() >= deadline:
            return TierResult(answer, scratchpad, False, "budget")

        tb = ta = 0
        latent_calls = 0
        rejected = 0
        truncated = False

        # --- optional retrieval: ground this step in a knowledge base ---
        snippets, retrieval_error = _retrieve(cfg, problem, scratchpad)
        context = prompts.format_context(snippets)

        # --- n latent updates:  z = net(x, y, z) ---
        for _ in range(cfg.n):
            res = client.complete(
                model=tier.model,
                system=prompts.LATENT_SYSTEM,
                user=prompts.LATENT_UPDATE.format(
                    problem=problem,
                    context=context,
                    answer=answer or "(none yet)",
                    scratchpad=scratchpad or "(empty)",
                ),
                max_tokens=tier.max_tokens,
                temperature=cfg.temperature,
                use_headroom=cfg.use_headroom,
            )
            new_z = (res.text or "").strip()
            if new_z:
                scratchpad = new_z
            else:
                rejected += 1  # keep the previous scratchpad; never wipe memory
            truncated = truncated or getattr(res, "stop_reason", "") == "max_tokens"
            latent_calls += 1
            tb += res.tokens_before
            ta += res.tokens_after

        # --- 1 answer update:  y = net(y, z) ---
        res = client.complete(
            model=tier.model,
            system=prompts.ANSWER_SYSTEM,
            user=prompts.ANSWER_UPDATE.format(
                problem=problem, context=context, scratchpad=scratchpad, answer=answer or "(none yet)"
            ),
            max_tokens=tier.max_tokens,
            temperature=cfg.temperature,
            use_headroom=cfg.use_headroom,
        )
        new_answer = (res.text or "").strip()
        if new_answer:
            answer = new_answer
        else:
            rejected += 1  # keep the previous answer
        truncated = truncated or getattr(res, "stop_reason", "") == "max_tokens"
        tb += res.tokens_before
        ta += res.tokens_after

        norm = _norm(answer)
        converged = bool(norm) and norm in seen
        if norm:
            seen.add(norm)

        # --- oracle short-circuit ---
        validated, validator_error = _safe_validate(cfg.validator, answer)

        # --- halt predictor (Q-head) ---
        judge_calls = 0
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
                use_headroom=judge_headroom,
                votes=cfg.judge_votes,
            )
            halt_prob, reason = verdict.halt_prob, verdict.reason
            judge_calls = verdict.calls
            tb += verdict.tokens_before
            ta += verdict.tokens_after

        halted = validated or halt_prob >= cfg.halt_threshold
        stop_reason = "validated" if validated else ("halt" if halted else ("converged" if converged else "exhausted"))

        # Record the candidate BEFORE appending the step so best_step_index lines up.
        trace.note_candidate(answer=answer, halt_prob=halt_prob, model=tier.model)
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
                retrieved_snippets=len(snippets),
                retrieval_error=retrieval_error,
                rejected_updates=rejected,
                truncated=truncated,
                validator_error=validator_error,
                judge_calls=judge_calls,
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


def _retrieve(cfg: RecurseConfig, problem: str, scratchpad: str) -> tuple[list[str], str]:
    """Pull knowledge snippets for this step; never let retrieval break reasoning.

    Returns ``(snippets, error)`` — a failure yields no snippets plus a short error
    string for the trace, so a dead index is visible instead of silently ungrounded.
    """

    if cfg.retriever is None:
        return [], ""
    query = (problem + ("\n\n" + scratchpad if scratchpad else ""))[: cfg.retrieval_query_chars]
    try:
        snippets = cfg.retriever.retrieve(query, k=cfg.retrieval_k)
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    snippets = [s for s in (snippets or []) if s and s.strip()]
    return _bound_snippets(snippets, cfg.retrieval_max_chars), ""


def _bound_snippets(snippets: list[str], max_chars: int) -> list[str]:
    """Cap the total injected knowledge — backends can return unbounded blobs."""

    if not snippets:
        return snippets
    share = max(1, max_chars // len(snippets))
    out: list[str] = []
    total = 0
    for s in snippets:
        s = s.strip()
        room = min(share, max_chars - total)
        if room <= 0:
            break
        if len(s) > room:
            s = s[:room] + "…[truncated]"
        out.append(s)
        total += len(s)
    return out


def _same(a: str, b: str) -> bool:
    return _norm(a) == _norm(b)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _preview(s: str, n: int = 160) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _safe_validate(validator, answer: str) -> tuple[bool, str]:
    """Run the user's oracle; a buggy validator is recorded, never fatal."""

    if validator is None:
        return False, ""
    try:
        return bool(validator(answer)), ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
