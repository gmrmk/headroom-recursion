"""Configuration for the recursive-reasoning loop.

The defaults mirror the TRM paper where it maps cleanly: ``n = 6`` latent updates
per improvement step is the paper's default; ``T = 3`` improvement steps per tier is
a small inference-time budget (the paper allows up to N_sup=16 supervised steps at
train time, but each LLM step is far more expensive, so we keep it low and lean on
tier escalation instead).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


# Accurate model IDs for the Claude family, cheapest -> most capable. This ordering
# *is* the "less is more" ladder: recurse on the tiny/cheap model first and only
# escalate when it plateaus.
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"
OPUS = "claude-opus-4-8"
FABLE = "claude-fable-5"


@dataclass(frozen=True)
class Tier:
    """One rung of the escalation ladder."""

    model: str
    # Per-tier max improvement steps. Defaults to the config's ``T`` when None.
    max_steps: Optional[int] = None
    # Token cap per Claude call at this tier.
    max_tokens: int = 2048


DEFAULT_LADDER: tuple[Tier, ...] = (
    Tier(HAIKU),
    Tier(SONNET),
    Tier(OPUS),
    Tier(FABLE),
)


# An optional oracle: given a candidate answer string, return True if it is provably
# correct/complete (e.g. a solved Sudoku grid validates). When supplied and it
# returns True, the loop halts immediately regardless of the self-eval judge.
Validator = Callable[[str], bool]


@dataclass
class RecurseConfig:
    """Knobs for one recursive-reasoning run."""

    # Recursion shape (TRM: n latent updates, then 1 answer update, per step).
    n: int = 6
    T: int = 3

    # Escalation ladder, cheapest first.
    ladder: tuple[Tier, ...] = DEFAULT_LADDER

    # Self-eval halt threshold in [0, 1]. Halt when judged halt_prob >= this.
    halt_threshold: float = 0.9

    # If set, the halt judge always runs on this model instead of the current tier.
    # Pin to HAIKU to keep judging cheap; leave None to judge with the working tier.
    judge_model: Optional[str] = None

    # Headroom context compression. When True the message list is compressed before
    # every Claude request. Silently no-ops if headroom-ai is not installed.
    use_headroom: bool = True

    # Optional oracle for structured tasks (see ``Validator``).
    validator: Optional[Validator] = None

    # Sampling temperature for the working-model calls.
    temperature: float = 0.7

    def steps_for(self, tier: Tier) -> int:
        return tier.max_steps if tier.max_steps is not None else self.T
