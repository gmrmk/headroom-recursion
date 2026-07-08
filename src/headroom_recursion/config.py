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
    # Escalate past this tier when a single improvement step takes longer than
    # this many seconds (checked when the step completes). A *hung call* is the
    # transport client's job (see clients.CLITransportClient); this guards
    # against a tier that is merely too slow to be worth its steps.
    step_timeout_s: Optional[float] = None


DEFAULT_LADDER: tuple[Tier, ...] = (
    Tier(HAIKU),
    Tier(SONNET),
    Tier(OPUS),
    Tier(FABLE),
)

# For research-mode runs (graded rubrics, open problems, anything where partial
# credit exists): live-run data shows fabrication pressure rises as capability
# falls — the cheapest tier fabricated citations in 4/4 graded steps where
# Sonnet+ did not. Start research ladders at Sonnet; use Haiku only where a
# mechanical oracle checks its work.
RESEARCH_LADDER: tuple[Tier, ...] = (
    Tier(SONNET),
    Tier(OPUS),
    Tier(FABLE),
)


@dataclass(frozen=True)
class Verdict:
    """A structured validator result for claims reality hasn't graded yet.

    ``settles_at`` (ISO date) marks the verdict PROVISIONAL: the mechanical check
    passed today (coherence, backtest, simulation), but the claim is about the
    future and only settles when the date arrives. The trace carries it forward
    so a scheduler can re-grade the claim against reality on settlement day.
    """

    passed: bool
    settles_at: Optional[str] = None
    note: str = ""
    # Verdict strength within a rung: 1.0 = exhaustive/deterministic check;
    # < 1.0 = statistical (e.g. Monte Carlo) — a validated halt below 1.0 is
    # flagged for human review and never masquerades as exhaustive.
    confidence: float = 1.0


# An optional oracle: given a candidate answer string, return True (or a Verdict)
# if it is provably correct/complete (e.g. a solved Sudoku grid validates). When
# it passes, the loop halts immediately regardless of the self-eval judge.
Validator = Callable[[str], "bool | Verdict"]


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

    # Oracle Compiler (oracle.py): when True and no validator was supplied, step
    # zero compiles + sandbox-calibrates a mechanical verifier for the problem.
    # A validator that fails calibration is demoted — the judge keeps authority.
    oracle_auto: bool = False
    # Model that compiles the oracle. None -> the strongest model in the ladder.
    oracle_model: Optional[str] = None
    # Sandbox timeout per validator invocation.
    oracle_timeout_s: float = 10.0
    # Internal: set by the compile step — one line telling the judge what the
    # oracle verifies and what residuals remain for the judge to score.
    oracle_note: str = ""
    # Internal: verification rung of a hand-wired validator (e.g. 1 for the Lean
    # oracle), copied into the trace so summaries report it. 0 = unknown/unset.
    oracle_rung: int = 0
    # Internal: halt authority of the installed validator. True (default, and
    # always true for hand-supplied validators) = a pass halts as "validated".
    # False (compiled oracle that declared itself insufficient) = GATE mode:
    # fails are final for the step, passes defer to the judge.
    oracle_sufficient: bool = True

    # Oracle feedback (CEGIS-style counterexample-guided repair): called with each
    # step's answer AFTER the halt decision; whatever string it returns is appended
    # to the scratchpad as [ORACLE FEEDBACK] so the NEXT step refines against
    # mechanical findings (compiler errors, failing test cases, counterexamples).
    # The canonical use: Lean proof-repair — feedback = the compiler's error output.
    feedback: Optional[Callable[[str], str]] = None

    # Claim auditing (claims.py): parse [KNOWN]/[NEW] claims from each answer,
    # resolve [KNOWN] citations against the retriever (unresolvable -> UNSOURCED)
    # and hunt prior art for [NEW] labels. Requires a retriever to have teeth.
    claim_audit: bool = False

    # Initial scratchpad content — e.g. verified claims loaded from a run ledger,
    # so later runs build on settled ground instead of re-deriving (or worse,
    # re-fabricating) it.
    seed_scratchpad: str = ""
    # Initial ANSWER candidate. Live-run finding: seeding only the scratchpad
    # hands prior top-tier work to the cheapest tier for reconstruction, which
    # measurably degrades it before better tiers recover. Seeding the answer
    # makes the incumbent the current candidate — tiers refine, never rebuild,
    # and the best-answer rail stops them making it worse.
    seed_answer: str = ""

    # Optional retrieval layer (e.g. a LightRAG-backed knowledge base). When set, each
    # improvement step retrieves relevant snippets and injects them into the prompts so
    # the recursion is grounded in external knowledge. Any object with a
    # ``retrieve(query, k) -> list[str]`` method works (see ``retrieval.Retriever``).
    retriever: Optional["Retriever"] = None
    # Retriever used by the CLAIM AUDIT (citation firewall / novelty triage).
    # None -> fall back to ``retriever``. Set this to an exact-match backend
    # (e.g. CorpusRetriever) when ``retriever`` is fuzzy: a backend that
    # returns loosely-related context for any query would "resolve" fabricated
    # citations and defang the firewall.
    audit_retriever: Optional["Retriever"] = None
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
            if tier.step_timeout_s is not None and tier.step_timeout_s <= 0:
                bad(f"tier {tier.model}: step_timeout_s must be > 0 (got {tier.step_timeout_s})")
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
        if self.oracle_timeout_s <= 0:
            bad(f"oracle_timeout_s must be > 0 (got {self.oracle_timeout_s})")
