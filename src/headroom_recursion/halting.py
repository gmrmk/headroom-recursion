"""The halt predictor — the LLM analogue of TRM's Q-head.

TRM learns a small head that predicts "have I reached the correct solution?" and
stops recursing when confident. Here a strict verifier call plays that role: it
returns a ``halt_prob`` in [0, 1]. The loop halts when that probability crosses the
configured threshold. Two cheaper signals short-circuit the judge:

* an optional user ``validator`` (an oracle for structured answers), and
* convergence — the answer text is unchanged across two consecutive steps.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from headroom_recursion import prompts
from headroom_recursion.claude import CallResult


@dataclass
class HaltVerdict:
    halt_prob: float
    reason: str
    tokens_before: int = 0
    tokens_after: int = 0


def judge(
    client,
    *,
    model: str,
    problem: str,
    answer: str,
    scratchpad: str,
    max_tokens: int,
    use_headroom: bool,
) -> HaltVerdict:
    """Ask the verifier model to score the candidate answer."""

    res: CallResult = client.complete(
        model=model,
        system=prompts.HALT_SYSTEM,
        user=prompts.HALT_JUDGE.format(problem=problem, answer=answer, scratchpad=scratchpad),
        max_tokens=max_tokens,
        temperature=0.0,
        use_headroom=use_headroom,
    )
    prob, reason = _parse(res.text)
    return HaltVerdict(prob, reason, res.tokens_before, res.tokens_after)


def _parse(text: str) -> tuple[float, str]:
    """Pull ``halt_prob`` (and a reason) out of the judge's reply, defensively."""

    obj = _first_json_object(text)
    if obj is not None:
        prob = obj.get("halt_prob", obj.get("prob"))
        reason = str(obj.get("reason", ""))
        if isinstance(prob, (int, float)):
            return _clamp(float(prob)), reason

    # Fallback: grab the first float in the text.
    m = re.search(r"(\d*\.\d+|\d+)", text)
    if m:
        try:
            return _clamp(float(m.group(1))), text.strip()[:120]
        except ValueError:
            pass
    return 0.0, "unparseable judge reply"


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
