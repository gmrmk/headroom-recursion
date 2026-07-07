"""Structured run trace — the observable record of a recursive-reasoning run."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class StepTrace:
    """One improvement step (n latent updates + 1 answer update + 1 halt judge)."""

    tier_model: str
    step_index: int  # 0-based, within the whole run
    latent_calls: int
    answer_preview: str
    halt_prob: float
    halted: bool
    converged: bool
    reason: str = ""
    # Headroom accounting for this step, summed across its requests.
    tokens_before: int = 0
    tokens_after: int = 0

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)


@dataclass
class RunTrace:
    """The full record: final answer, why it stopped, and every step."""

    problem: str
    final_answer: str = ""
    halted: bool = False
    stop_reason: str = ""  # "halt" | "converged" | "validated" | "exhausted"
    final_model: str = ""
    steps: list[StepTrace] = field(default_factory=list)

    def add(self, step: StepTrace) -> None:
        self.steps.append(step)

    @property
    def total_calls(self) -> int:
        # Each step: latent_calls + 1 answer + 1 halt judge.
        return sum(s.latent_calls + 2 for s in self.steps)

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
        tiers = " -> ".join(_dedupe_consecutive(s.tier_model for s in self.steps))
        lines = [
            f"stop_reason : {self.stop_reason} (halted={self.halted})",
            f"final_model : {self.final_model}",
            f"tier path   : {tiers or '(none)'}",
            f"steps       : {len(self.steps)}   claude calls: {self.total_calls}",
        ]
        if self.tokens_before:
            lines.append(
                f"headroom    : {self.tokens_before} -> {self.tokens_after} tokens "
                f"({self.savings_pct:.0f}% saved)"
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
