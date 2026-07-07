"""Configuration for the recursive-reasoning loop.

The defaults mirror the TRM paper where it maps cleanly: ``n = 6`` latent updates
per improvement step is the paper's default; ``T = 3`` improvement steps per tier is
a small inference-time budget (the paper allows up to N_sup=16 supervised steps at
train time, but each LLM step is far more expensive, so we keep it low and lean on
tier escalation instead).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # avoid a runtime import cycle (retrieval imports claude/config helpers)
    from headroom_recursion.retrieval import Retriever


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
    # Pin to HAIKU to keep judging cheap; leave None to judge with the working tier
    # (note the self-preference caveat: a model grading its own answer skews high).
    judge_model: Optional[str] = None
    # Independent judge calls per step; the MEDIAN halt_prob wins. >1 is robust to a
    # single sycophantic vote at the cost of extra (cheap, if pinned) judge calls.
    judge_votes: int = 1
    # The judge verifies the exact answer text, so its input is NOT compressed by
    # default — a lossy paraphrase of the thing being verified defeats verification.
    compress_judge: bool = False

    # Headroom context compression. When True the message list is compressed before
    # every Claude request. Silently no-ops if headroom-ai is not installed.
    use_headroom: bool = True

    # Optional oracle for structured tasks (see ``Validator``).
    validator: Optional[Validator] = None

    # Optional retrieval layer (e.g. a LightRAG-backed knowledge base). When set, each
    # improvement step retrieves relevant snippets and injects them into the prompts so
    # the recursion is grounded in external knowledge. Any object with a
    # ``retrieve(query, k) -> list[str]`` method works (see ``retrieval.Retriever``).
    retriever: Optional["Retriever"] = None
    # How many snippets to request per retrieval.
    retrieval_k: int = 4
    # Max characters of (problem + scratchpad) used to form the retrieval query.
    retrieval_query_chars: int = 1200
    # Max characters of retrieved knowledge injected per step (snippets are truncated
    # to fit). Retrieval backends can return unbounded blobs; this is the safety cap.
    retrieval_max_chars: int = 8000

    # Sampling temperature for the working-model calls.
    temperature: float = 0.7

    # Hard budgets — the run stops (keeping its best answer, never escalating) when
    # either is exceeded. Checked at step boundaries, so overshoot is at most one
    # step (n + 1 + judge_votes calls).
    max_total_calls: Optional[int] = None
    max_wall_seconds: Optional[float] = None

    def steps_for(self, tier: Tier) -> int:
        return tier.max_steps if tier.max_steps is not None else self.T

    def validate(self) -> None:
        """Fail fast on configs that silently misbehave (never halt, never reason)."""

        def bad(msg: str) -> None:
            raise ValueError(f"RecurseConfig: {msg}")

        if self.n < 1:
            bad(f"n must be >= 1 (got {self.n}) — 0 latent updates means no reasoning")
        if self.T < 1:
            bad(f"T must be >= 1 (got {self.T})")
        for tier in self.ladder:
            if tier.max_steps is not None and tier.max_steps < 1:
                bad(f"tier {tier.model}: max_steps must be >= 1 (got {tier.max_steps})")
            if tier.max_tokens < 1:
                bad(f"tier {tier.model}: max_tokens must be >= 1 (got {tier.max_tokens})")
        if not (0.0 < self.halt_threshold <= 1.0):
            bad(
                f"halt_threshold must be in (0, 1] (got {self.halt_threshold}) — "
                "above 1.0 the run can never halt"
            )
        if not (0.0 <= self.temperature <= 1.0):
            bad(f"temperature must be in [0, 1] (got {self.temperature})")
        if self.judge_votes < 1:
            bad(f"judge_votes must be >= 1 (got {self.judge_votes})")
        if self.retriever is not None and self.retrieval_k < 1:
            bad(f"retrieval_k must be >= 1 (got {self.retrieval_k})")
        if self.retrieval_query_chars < 1:
            bad(f"retrieval_query_chars must be >= 1 (got {self.retrieval_query_chars})")
        if self.retrieval_max_chars < 1:
            bad(f"retrieval_max_chars must be >= 1 (got {self.retrieval_max_chars})")
        if self.max_total_calls is not None and self.max_total_calls < 1:
            bad(f"max_total_calls must be >= 1 (got {self.max_total_calls})")
        if self.max_wall_seconds is not None and self.max_wall_seconds <= 0:
            bad(f"max_wall_seconds must be > 0 (got {self.max_wall_seconds})")
