"""Prompt templates for the three recursion operations.

These are the LLM analogues of TRM's two learned functions:

* ``LATENT_UPDATE`` == ``z = net(x, y, z)`` — refine the reasoning scratchpad given the
  problem, the current answer, and the prior scratchpad.
* ``ANSWER_UPDATE`` == ``y = net(y, z)`` — rewrite the answer from the refined scratchpad.
* ``HALT_JUDGE`` == the ``Q_head`` — predict whether the current answer is correct/complete.
"""

from __future__ import annotations

LATENT_SYSTEM = (
    "You are the reasoning core of a recursive solver. You do NOT emit the final "
    "answer. You maintain a running scratchpad of analysis: constraints, deductions, "
    "suspected errors in the current candidate answer, and the single most useful "
    "next step. Be concise and cumulative — improve the prior scratchpad, don't repeat it."
)

LATENT_UPDATE = """\
PROBLEM:
{problem}

CURRENT CANDIDATE ANSWER:
{answer}

REASONING SCRATCHPAD SO FAR:
{scratchpad}

Critique the current candidate answer against the problem. Find the most important
flaw, gap, or unverified assumption. Update the scratchpad with the sharpest next
deduction. Output ONLY the revised scratchpad (no answer, no preamble)."""


ANSWER_SYSTEM = (
    "You produce the best possible answer to the problem using the reasoning "
    "scratchpad. Output ONLY the answer itself, in the exact form the problem asks "
    "for — no explanation, no restating the scratchpad."
)

ANSWER_UPDATE = """\
PROBLEM:
{problem}

REASONING SCRATCHPAD:
{scratchpad}

PREVIOUS CANDIDATE ANSWER:
{answer}

Produce the improved final answer. Output ONLY the answer."""


HALT_SYSTEM = (
    "You are a strict verifier. Decide whether the candidate answer is correct AND "
    "complete for the problem. Respond with a single line of JSON and nothing else: "
    '{"halt_prob": <float 0..1>, "reason": "<short>"}. Use halt_prob=1.0 only when '
    "you have verified the answer is fully correct; use low values when anything is "
    "unchecked or wrong."
)

HALT_JUDGE = """\
PROBLEM:
{problem}

CANDIDATE ANSWER:
{answer}

REASONING SCRATCHPAD:
{scratchpad}

Verify the candidate answer. Respond with ONLY the JSON line."""
