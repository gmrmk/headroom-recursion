"""The halt predictor — the LLM analogue of TRM's Q-head.

TRM learns a small head that predicts "have I reached the correct solution?" and
stops recursing when confident. Here a strict verifier call plays that role: it
returns a ``halt_prob`` in [0, 1]. The loop halts when that probability crosses the
configured threshold. Two cheaper signals short-circuit the judge:

* an optional user ``validator`` (an oracle for structured answers), and
* convergence — the answer text repeats an earlier answer (the tier has stalled).

Robustness rules (hard-won):
* A number in the judge's prose is only trusted if it is already in [0, 1]. Clamping
  arbitrary numbers turns "I found 3 errors" into halt_prob 1.0 — a false halt on a
  wrong answer, inverting the verdict exactly when the judge is most critical.
* An unparseable reply gets ONE re-ask before being treated as "don't halt" (0.0).
* With ``votes > 1``, the judge runs that many times and the MEDIAN wins — robust to
  a single sycophantic outlier when a model grades its own work.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass

from headroom_recursion import prompts
from headroom_recursion.claude import CallResult

_UNPARSEABLE = "unparseable judge reply"


@dataclass
class HaltVerdict:
    halt_prob: float
    reason: str
    tokens_before: int = 0
    tokens_after: int = 0
    calls: int = 1  # actual judge calls made (votes + parse retries)


def judge(
    client,
    *,
    model: str,
    problem: str,
    answer: str,
    scratchpad: str,
    max_tokens: int,
    use_headroom: bool,
    votes: int = 1,
) -> HaltVerdict:
    """Ask the verifier model to score the candidate answer.

    ``votes > 1`` runs independent judge calls (slightly warmed temperature so they
    can differ) and returns the median probability.
    """

    votes = max(1, votes)
    temperature = 0.0 if votes == 1 else 0.3
    user = prompts.HALT_JUDGE.format(problem=problem, answer=answer, scratchpad=scratchpad)

    results: list[tuple[float, str]] = []
    tb = ta = 0
    n_calls = 0
    for _ in range(votes):
        res: CallResult = client.complete(
            model=model,
            system=prompts.HALT_SYSTEM,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
            use_headroom=use_headroom,
        )
        tb += res.tokens_before
        ta += res.tokens_after
        n_calls += 1
        prob, reason = _parse(res.text)

        if reason == _UNPARSEABLE:
            # One re-ask (cold) before accepting "don't halt" — only fires on garbage.
            res = client.complete(
                model=model,
                system=prompts.HALT_SYSTEM,
                user=user,
                max_tokens=max_tokens,
                temperature=0.0,
                use_headroom=use_headroom,
            )
            tb += res.tokens_before
            ta += res.tokens_after
            n_calls += 1
            prob, reason = _parse(res.text)

        results.append((prob, reason))

    med = statistics.median(p for p, _ in results)
    # Report the reason of the vote closest to the returned probability.
    reason = min(results, key=lambda pr: abs(pr[0] - med))[1]
    return HaltVerdict(float(med), reason, tb, ta, n_calls)


def _parse(text: str) -> tuple[float, str]:
    """Pull ``halt_prob`` (and a reason) out of the judge's reply, defensively."""

    obj = _first_json_object(text)
    if obj is not None:
        prob = obj.get("halt_prob", obj.get("prob"))
        reason = str(obj.get("reason", ""))
        if isinstance(prob, (int, float)):
            return _clamp(float(prob)), reason

    # Fallback: the first number in the prose — trusted ONLY if it is already a
    # probability. Out-of-range numbers ("3 errors", "85 out of 100") say nothing
    # about confidence and must never be clamped into a halt signal.
    m = re.search(r"(\d*\.\d+|\d+)", text)
    if m:
        try:
            v = float(m.group(1))
        except ValueError:
            v = None
        if v is not None and 0.0 <= v <= 1.0:
            return v, text.strip()[:120]
    return 0.0, _UNPARSEABLE


def _first_json_object(text: str) -> dict | None:
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))
