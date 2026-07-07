"""Shared test fixtures — a network-free stub Claude client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Union

import pytest

from headroom_recursion import prompts
from headroom_recursion.claude import CallResult


def _kind(system: str) -> str:
    if system == prompts.LATENT_SYSTEM:
        return "latent"
    if system == prompts.ANSWER_SYSTEM:
        return "answer"
    if system == prompts.HALT_SYSTEM:
        return "judge"
    return "other"


@dataclass
class StubClient:
    """Records every call and returns scripted outputs.

    * ``answers``  — list of answer strings returned in order for answer updates
      (last value repeats once exhausted). Defaults to a monotonically changing
      answer so convergence does not trigger by accident.
    * ``halt_prob`` — constant, or a callable ``(step_index) -> float`` where
      ``step_index`` counts answer updates produced so far (0-based).
    * ``latent_texts`` — scratchpad strings returned in order for latent calls
      (last repeats); default is a generic non-empty scratchpad.
    * ``judge_texts`` — raw judge replies returned in order (last repeats);
      overrides ``halt_prob`` when set.
    * ``raise_on_call`` / ``raise_exc`` — raise ``raise_exc`` on the Nth call
      (1-based, counted across all kinds) to script mid-run failures.
    * ``tokens_before`` / ``tokens_after`` — reported per call for trace accounting.
    """

    answers: Optional[list[str]] = None
    halt_prob: Union[float, Callable[[int], float]] = 0.0
    latent_texts: Optional[list[str]] = None
    judge_texts: Optional[list[str]] = None
    raise_on_call: Optional[int] = None
    raise_exc: type = RuntimeError
    tokens_before: int = 100
    tokens_after: int = 40
    calls: list[tuple[str, str]] = field(default_factory=list)  # (kind, model)
    prompts_seen: list[tuple[str, str]] = field(default_factory=list)  # (kind, user prompt)
    _answer_idx: int = 0
    _latent_idx: int = 0
    _judge_idx: int = 0

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        use_headroom: bool = True,
    ) -> CallResult:
        kind = _kind(system)
        self.calls.append((kind, model))
        self.prompts_seen.append((kind, user))

        if self.raise_on_call is not None and len(self.calls) >= self.raise_on_call:
            raise self.raise_exc(f"stub failure at call {len(self.calls)}")

        if kind == "answer":
            if self.answers:
                idx = min(self._answer_idx, len(self.answers) - 1)
                text = self.answers[idx]
            else:
                text = f"answer-v{self._answer_idx}"
            self._answer_idx += 1
            return CallResult(text, self.tokens_before, self.tokens_after)

        if kind == "judge":
            if self.judge_texts:
                idx = min(self._judge_idx, len(self.judge_texts) - 1)
                self._judge_idx += 1
                return CallResult(self.judge_texts[idx], self.tokens_before, self.tokens_after)
            step = max(0, self._answer_idx - 1)
            p = self.halt_prob(step) if callable(self.halt_prob) else self.halt_prob
            return CallResult(f'{{"halt_prob": {p}, "reason": "stub"}}', self.tokens_before, self.tokens_after)

        if kind == "latent" and self.latent_texts is not None:
            idx = min(self._latent_idx, len(self.latent_texts) - 1)
            self._latent_idx += 1
            return CallResult(self.latent_texts[idx], self.tokens_before, self.tokens_after)

        # latent / other
        return CallResult(f"scratchpad after {kind} call", self.tokens_before, self.tokens_after)

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)

    def models_used(self) -> list[str]:
        seen: list[str] = []
        for _, m in self.calls:
            if not seen or seen[-1] != m:
                seen.append(m)
        return seen


@dataclass
class StubRetriever:
    """Records queries and returns fixed snippets."""

    snippets: list[str] = field(default_factory=lambda: ["FACT: the sky is blue."])
    queries: list[tuple[str, int]] = field(default_factory=list)

    def retrieve(self, query: str, *, k: int) -> list[str]:
        self.queries.append((query, k))
        return list(self.snippets)


@pytest.fixture
def stub():
    return StubClient()
