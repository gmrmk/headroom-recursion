"""Structured run trace — the observable record of a recursive-reasoning run."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class StepTrace:
    """One improvement step (n latent updates + 1 answer update + halt judging)."""

    tier_model: str
    step_index: int  # 0-based, within the whole run
    latent_calls: int
    answer_preview: str
    halt_prob: float
    halted: bool
    converged: bool
    reason: str = ""
    # Number of knowledge snippets retrieved and injected this step (0 if no retriever).
    retrieved_snippets: int = 0
    # Why retrieval returned nothing, when it failed (empty = no failure).
    retrieval_error: str = ""
    # Claim audit (rung 4): [KNOWN] claims whose citations failed to resolve, and
    # [NEW] claims with candidate prior art in the corpus.
    unsourced_claims: int = 0
    flagged_new_claims: int = 0
    # True when a gate-mode oracle mechanically rejected this step's answer
    # (the judge was skipped; halt_prob is 0.0 by rejection, not by opinion).
    gate_rejected: bool = False
    # Model outputs rejected this step (empty/whitespace completions that would have
    # destroyed the scratchpad or answer; the previous value was kept instead).
    rejected_updates: int = 0
    # True if any call this step was cut off at max_tokens (its output may be partial).
    truncated: bool = False
    # A validator that raised (recorded, never fatal; empty = no failure).
    validator_error: str = ""
    # Judge calls this step (0 when the validator short-circuits; >1 with votes/retry).
    judge_calls: int = 1
    # Headroom accounting for this step, summed across its requests.
    tokens_before: int = 0
    tokens_after: int = 0

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)


@dataclass
class RunTrace:
    """The full record: final answer, why it stopped, and every step.

    ``stop_reason`` vocabulary:
      halt | validated  — confident stop; ``final_answer`` is the halting answer
      converged | exhausted | step-timeout — ladder ran out; ``final_answer`` is the
                          BEST-scoring answer
      budget            — a hard budget hit; best answer, no escalation past the hit
      error | interrupted — run cut short; best answer so far, ``error`` says why
      no-op             — empty ladder
    """

    problem: str
    final_answer: str = ""
    halted: bool = False
    stop_reason: str = ""
    final_model: str = ""
    # Best-scoring answer seen anywhere in the run (refinement is not monotone).
    best_answer: str = ""
    best_halt_prob: float = -1.0
    best_step_index: int = -1
    best_model: str = ""
    # Why each tier ended, e.g. "claude-haiku-...: converged".
    tier_stops: list[str] = field(default_factory=list)
    # Set when the run stopped abnormally (stop_reason "error"/"interrupted").
    error: str = ""
    wall_seconds: float = 0.0
    # Oracle Compiler record: rung 0 = no oracle attempted; 5 = demoted to judge.
    oracle_rung: int = 0
    oracle_residuals: list[str] = field(default_factory=list)
    oracle_calls: int = 0
    # True when the installed validator is a GATE (insufficient): its passes
    # defer to the judge; only its rejections are mechanical.
    oracle_gate_only: bool = False
    # Confidence of the verdict behind a "validated" stop (1.0 = exhaustive;
    # < 1.0 = statistical, which flags human review).
    validated_confidence: float = 1.0
    # True when the outcome rests on judged opinion at a score high enough to
    # matter (>= 0.40) — i.e. NOT mechanically validated. Read it before believing.
    needs_human_review: bool = False
    # Set when a Verdict-returning validator passed provisionally: the mechanical
    # check succeeded, but the claim is about the future and settles on this date.
    settles_at: str = ""
    steps: list[StepTrace] = field(default_factory=list)

    def add(self, step: StepTrace) -> None:
        self.steps.append(step)

    def trajectory(self) -> str:
        """Per-step halt_probs grouped by tier: 'sonnet 0.25 0.28 | opus 0.30'."""

        parts: list[str] = []
        tier, scores = None, []
        for s in self.steps:
            short = s.tier_model.split("-")[1] if "-" in s.tier_model else s.tier_model
            if short != tier and scores:
                parts.append(f"{tier} {' '.join(scores)}")
                scores = []
            tier = short
            scores.append(f"{s.halt_prob:.2f}")
        if scores:
            parts.append(f"{tier} {' '.join(scores)}")
        return " | ".join(parts)

    def note_candidate(self, *, answer: str, halt_prob: float, model: str) -> None:
        """Record a candidate answer; keeps the best (ties go to the latest)."""

        if halt_prob >= self.best_halt_prob and answer:
            self.best_answer = answer
            self.best_halt_prob = halt_prob
            self.best_step_index = len(self.steps)  # index of the step being recorded
            self.best_model = model

    @property
    def total_calls(self) -> int:
        # Per step: latent calls + 1 answer + actual judge calls, plus any oracle
        # compilation calls. Retriever-internal LLM calls (e.g. LightRAG entity
        # extraction) are NOT counted — see docs.
        return sum(s.latent_calls + 1 + s.judge_calls for s in self.steps) + self.oracle_calls

    @property
    def tokens_before(self) -> int:
        return sum(s.tokens_before for s in self.steps)

    @property
    def tokens_after(self) -> int:
        return sum(s.tokens_after for s in self.steps)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def savings_pct(self) -> float:
        if self.tokens_before <= 0:
            return 0.0
        return 100.0 * self.tokens_saved / self.tokens_before

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_calls"] = self.total_calls
        d["tokens_before"] = self.tokens_before
        d["tokens_after"] = self.tokens_after
        d["tokens_saved"] = self.tokens_saved
        d["savings_pct"] = round(self.savings_pct, 1)
        return d

    def summary(self) -> str:
        tiers = " -> ".join(self.tier_stops) if self.tier_stops else " -> ".join(
            _dedupe_consecutive(s.tier_model for s in self.steps)
        )
        stop = self.stop_reason
        if stop == "validated" and self.validated_confidence < 1.0:
            stop += f" (STATISTICAL, confidence={self.validated_confidence:g} — not exhaustive)"
        lines = [
            f"stop_reason : {stop} (halted={self.halted})",
            f"final_model : {self.final_model}",
            f"tier path   : {tiers or '(none)'}",
            f"steps       : {len(self.steps)}   claude calls: {self.total_calls}"
            + (f"   wall: {self.wall_seconds:.1f}s" if self.wall_seconds else ""),
        ]
        if self.steps:
            lines.append(f"trajectory  : {self.trajectory()}")
        if self.error:
            lines.append(f"error       : {self.error}")
        if self.oracle_rung:
            if self.oracle_rung <= 3:
                what = "calibrated validator installed" + (", GATE only" if self.oracle_gate_only else "")
            else:
                what = "demoted — judge only"
            lines.append(f"oracle      : rung {self.oracle_rung} ({what})")
            rejected = sum(1 for s in self.steps if s.gate_rejected)
            if rejected:
                lines.append(f"gate        : {rejected} answer(s) mechanically rejected (judge skipped)")
            if self.oracle_residuals:
                lines.append(f"residuals   : {'; '.join(self.oracle_residuals)[:200]}")
        if self.needs_human_review:
            lines.append(
                "review      : NEEDS HUMAN REVIEW — outcome rests on judged opinion, "
                "not mechanical verification"
            )
        if self.settles_at:
            lines.append(
                f"settlement  : PROVISIONAL — validated today, settles against reality on {self.settles_at}"
            )
        unsourced = sum(s.unsourced_claims for s in self.steps)
        flagged = sum(s.flagged_new_claims for s in self.steps)
        if unsourced or flagged:
            lines.append(
                f"claim audit : {unsourced} unsourced citation claim(s), "
                f"{flagged} [NEW] claim(s) with candidate prior art"
            )
        if self.tokens_before:
            lines.append(
                f"headroom    : {self.tokens_before} -> {self.tokens_after} tokens "
                f"({self.savings_pct:.0f}% saved)"
            )
        retrieved = sum(s.retrieved_snippets for s in self.steps)
        if retrieved:
            lines.append(f"retrieval   : {retrieved} snippets injected across {len(self.steps)} steps")
        rejected = sum(s.rejected_updates for s in self.steps)
        if rejected:
            lines.append(f"warnings    : {rejected} empty completion(s) rejected (state preserved)")
        if any(s.truncated for s in self.steps):
            lines.append("warnings    : some calls hit max_tokens (output may be partial)")
        if (
            not self.halted
            and self.best_step_index >= 0
            and self.steps
            and self.best_step_index != len(self.steps) - 1
        ):
            lines.append(
                f"note        : answer taken from step {self.best_step_index} "
                f"(best halt_prob {self.best_halt_prob:.2f}), not the last step"
            )
        lines.append("")
        lines.append("ANSWER:")
        lines.append(self.final_answer)
        return "\n".join(lines)


def _dedupe_consecutive(items) -> list[str]:
    out: list[str] = []
    for it in items:
        if not out or out[-1] != it:
            out.append(it)
    return out
