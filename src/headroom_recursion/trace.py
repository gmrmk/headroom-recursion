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
      converged | exhausted — ladder ran out; ``final_answer`` is the BEST-scoring answer
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
    steps: list[StepTrace] = field(default_factory=list)

    def add(self, step: StepTrace) -> None:
        self.steps.append(step)

    def note_candidate(self, *, answer: str, halt_prob: float, model: str) -> None:
        """Record a candidate answer; keeps the best (ties go to the latest)."""

        if halt_prob >= self.best_halt_prob and answer:
            self.best_answer = answer
            self.best_halt_prob = halt_prob
            self.best_step_index = len(self.steps)  # index of the step being recorded
            self.best_model = model

    @property
    def total_calls(self) -> int:
        # Per step: latent calls + 1 answer + actual judge calls. Retriever-internal
        # LLM calls (e.g. LightRAG entity extraction) are NOT counted — see docs.
        return sum(s.latent_calls + 1 + s.judge_calls for s in self.steps)

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
        lines = [
            f"stop_reason : {self.stop_reason} (halted={self.halted})",
            f"final_model : {self.final_model}",
            f"tier path   : {tiers or '(none)'}",
            f"steps       : {len(self.steps)}   claude calls: {self.total_calls}"
            + (f"   wall: {self.wall_seconds:.1f}s" if self.wall_seconds else ""),
        ]
        if self.error:
            lines.append(f"error       : {self.error}")
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
