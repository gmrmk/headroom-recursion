"""Per-call liveness, phase, and cost telemetry for long-running loops.

``HeartbeatClient`` wraps any ``CompletionClient``: every model call in
trm/halting/oracle flows through ``complete()``, so wrapping the client gives
per-call heartbeats with zero changes to the loop. The beat file is written
atomically (tmp + ``os.replace``, same as the ledger) BEFORE each call starts —
so during a long call the heartbeat's age equals the call's age, and a stalled
transport is visible within one call interval — and again after it finishes
with updated totals.

The wrapper is also the dollar fuse: give it ``max_cost_usd`` and, once the
summed per-call ``cost_usd`` crosses it, the next call raises
``CostCapExceeded`` *before* spending anything. The ladder soft-fails the tier
and the run finalizes with its best answer — the cap can never strand a run
mid-thought, only stop it from starting new work.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from headroom_recursion.claude import CallResult


class CostCapExceeded(RuntimeError):
    """The cost cap was reached; raised BEFORE the next call spends anything."""


def _phase_of(system: str) -> str:
    from headroom_recursion import oracle, prompts

    if system == prompts.LATENT_SYSTEM:
        return "latent"
    if system == prompts.ANSWER_SYSTEM:
        return "answer"
    if system == prompts.HALT_SYSTEM:
        return "judge"
    if system == oracle.SYNTH_SYSTEM:
        return "oracle"
    return "other"


class HeartbeatClient:
    """Delegating ``CompletionClient`` that counts calls, sums real dollar cost,
    and writes a JSON pulse before and after every call."""

    def __init__(
        self,
        inner,
        path: Optional[str] = None,
        *,
        max_cost_usd: Optional[float] = None,
    ):
        self._inner = inner
        self._path = path
        self._cap = max_cost_usd
        self.calls = 0
        self.cost_usd = 0.0
        self.started = time.time()
        # Owner-visible context stamped into every beat (campaign run #, etc.).
        self.meta: dict[str, Any] = {}

    def complete(self, *, model: str, system: str, user: str, **kw) -> CallResult:
        if self._cap is not None and self.cost_usd >= self._cap:
            raise CostCapExceeded(
                f"cost cap ${self._cap:.2f} reached (${self.cost_usd:.2f} spent); "
                "not starting another call"
            )
        phase = _phase_of(system)
        self.beat(phase=phase, model=model, status="in-call")
        try:
            res = self._inner.complete(model=model, system=system, user=user, **kw)
        except Exception as exc:
            self.beat(phase=phase, model=model, status=f"error: {type(exc).__name__}")
            raise
        self.calls += 1
        self.cost_usd += float(getattr(res, "cost_usd", 0.0) or 0.0)
        self.beat(phase=phase, model=model, status="ok")
        return res

    def beat(self, **fields: Any) -> None:
        """Atomically write the pulse; a heartbeat must never crash the loop."""

        if not self._path:
            return
        payload = {
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 4),
            "uptime_s": round(time.time() - self.started, 1),
            **self.meta,
            **fields,
        }
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass
