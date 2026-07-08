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

from headroom_recursion import claims as claims_mod
from headroom_recursion import halting, prompts
from headroom_recursion.config import RecurseConfig, Tier, Verdict
from headroom_recursion.trace import RunTrace, StepTrace


@dataclass
class TierResult:
    answer: str
    scratchpad: str
    halted: bool
    # "validated" | "halt" | "converged" | "exhausted" | "budget" | "step-timeout"
    stop_reason: str


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
    # The judge (and only the judge) is told what the compiled oracle already
    # covers, so it scores the residuals instead of re-litigating verified ground.
    judge_problem = (
        f"{problem}\n\n[ORACLE STATUS] {cfg.oracle_note}" if cfg.oracle_note else problem
    )
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

        step_start = time.monotonic()
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

        # --- claim audit (rung 4): mechanical citation/novelty findings for the judge ---
        claim_note = ""
        unsourced = flagged_new = 0
        if cfg.claim_audit and cfg.retriever is not None:
            audited = claims_mod.audit_claims(claims_mod.parse_claims(answer), cfg.retriever)
            claim_note = claims_mod.judge_addendum(audited)
            unsourced = sum(1 for c in audited if c.label == "UNSOURCED")
            flagged_new = sum(1 for c in audited if c.label == "NEW" and c.prior_art)

        # --- oracle: decider or gate, per its declared sufficiency ---
        # A validator whose declared coverage excludes correctness (gate mode)
        # must never produce a "validated" halt: its passes defer to the judge,
        # and only its REJECTIONS are mechanical (final for the step, judge
        # skipped). Sufficient validators keep full halt authority.
        gate_rejected = False
        gate_note_text = ""
        if cfg.validator is not None and not cfg.oracle_sufficient:
            passed, validator_error, gate_verdict = _safe_validate(cfg.validator, answer)
            validated, verdict_obj = False, None
            if validator_error:
                # An ERRORED oracle checked nothing. It must never mechanically
                # reject — one broken toolchain would zero every step of a run.
                # The judge scores the step, told that the gate was down.
                gate_note_text = (
                    f"the mechanical gate ERRORED ({validator_error}) — nothing "
                    "was checked; correctness is entirely yours to score"
                )
            elif passed:
                detail = (
                    gate_verdict.note
                    if gate_verdict is not None and gate_verdict.note
                    else "format/constraints only — correctness is entirely yours to score"
                )
                gate_note_text = f"the mechanical gate PASSED ({detail})"
            else:
                gate_rejected = True
        else:
            validated, validator_error, verdict_obj = _safe_validate(cfg.validator, answer)
            if validated and verdict_obj is not None:
                if verdict_obj.settles_at:
                    trace.settles_at = verdict_obj.settles_at
                trace.validated_confidence = verdict_obj.confidence

        # --- halt predictor (Q-head) ---
        judge_calls = 0
        if validated:
            halt_prob, reason = 1.0, "validator confirmed"
        elif gate_rejected:
            halt_prob, reason = 0.0, "oracle gate rejected (mechanical; judge skipped)"
        else:
            gate_note = f"\n\n[ORACLE GATE] {gate_note_text}" if gate_note_text else ""
            verdict = halting.judge(
                client,
                model=judge_model,
                problem=judge_problem + (f"\n\n{claim_note}" if claim_note else "") + gate_note,
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
                unsourced_claims=unsourced,
                flagged_new_claims=flagged_new,
                gate_rejected=gate_rejected,
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

        # --- oracle feedback (CEGIS): mechanical findings guide the next step ---
        if cfg.feedback is not None:
            fb = _safe_feedback(cfg.feedback, answer)
            if fb:
                scratchpad = f"{scratchpad}\n\n[ORACLE FEEDBACK on the last answer]\n{fb}"

        if converged:
            # This tier has stopped moving — escalate rather than spin.
            return TierResult(answer, scratchpad, False, "converged")
        if tier.step_timeout_s is not None and time.monotonic() - step_start > tier.step_timeout_s:
            # Too slow to be worth its remaining steps — hand the draft upward.
            return TierResult(answer, scratchpad, False, "step-timeout")

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


def _norm(s: str) -> str:
    """Normalize an answer for convergence comparison.

    Case, whitespace, and markdown decoration (emphasis, inline code, heading
    and quote markers) are presentation, not content — an answer that repeats
    modulo formatting has still converged. Deliberately conservative beyond
    that: no fuzzy similarity, because a false ``converged`` forfeits the
    tier's remaining steps, and long research documents legitimately stay very
    similar between steps ("exhausted" escalation is their intended path).
    """

    s = (s or "").lower()
    s = re.sub(r"[`*_~]", "", s)
    s = re.sub(r"^[#>\s]+", "", s, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", s).strip()


def _preview(s: str, n: int = 160) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _safe_feedback(feedback, answer: str) -> str:
    """Run the feedback hook; a broken hook is silence, never a crash."""

    try:
        return (feedback(answer) or "").strip()[:4000]
    except Exception as exc:
        return f"(feedback hook failed: {type(exc).__name__}: {exc})"


def _safe_validate(validator, answer: str) -> tuple[bool, str, Optional[Verdict]]:
    """Run the user's oracle; a buggy validator is recorded, never fatal.

    Validators may return a plain bool or a structured ``Verdict`` (whose
    ``settles_at`` marks a provisional pass on a claim reality grades later).
    """

    if validator is None:
        return False, "", None
    try:
        result = validator(answer)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", None
    if isinstance(result, Verdict):
        return result.passed, "", result
    return bool(result), "", None
