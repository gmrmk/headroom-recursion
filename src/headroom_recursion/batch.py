"""Run the recursion loop over a *set* of problems — the multiplier.

Every value vector (competition math, formalization, open problems) needs the
same thing: run the loop over many problems (or the same problem many times, for
sample-wide majority voting) and aggregate. ``run_batch`` does exactly that,
reusing ``recurse`` unchanged, with a bounded thread pool (the CLI/SDK clients
are I/O-bound on the model call, so threads parallelize cleanly).

Nothing here trusts a model: each item's outcome is whatever its own trace says,
verified by whatever oracle its config carries.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from typing import Callable, Optional

from headroom_recursion.config import RecurseConfig
from headroom_recursion.ladder import RunError, recurse
from headroom_recursion.trace import RunTrace


@dataclass
class BatchItem:
    """One unit of work: a problem, its key, and an optional per-item config."""

    key: str
    problem: str
    config: Optional[RecurseConfig] = None  # falls back to the batch-wide config


@dataclass
class BatchResult:
    key: str
    problem: str
    trace: Optional[RunTrace]
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.trace is not None

    @property
    def halted(self) -> bool:
        return self.trace is not None and self.trace.halted

    @property
    def answer(self) -> str:
        return self.trace.final_answer if self.trace is not None else ""


def run_batch(
    items: list[BatchItem],
    *,
    client,
    config: Optional[RecurseConfig] = None,
    max_workers: int = 4,
    on_done: Optional[Callable[[BatchResult], None]] = None,
) -> list[BatchResult]:
    """Run every item through ``recurse`` concurrently; never let one kill the batch.

    A ``RunError`` (dead run) is captured as a result with its partial trace; any
    other exception is captured as an error string. Results come back in input
    order. ``on_done`` is called as each finishes (for live progress).
    """

    base = config or RecurseConfig()
    results: list[Optional[BatchResult]] = [None] * len(items)

    def work(idx: int, item: BatchItem) -> BatchResult:
        cfg = item.config or base
        try:
            trace = recurse(item.problem, client=client, config=cfg)
            return BatchResult(item.key, item.problem, trace)
        except RunError as exc:
            return BatchResult(item.key, item.problem, exc.trace, error=str(exc))
        except Exception as exc:  # never let one item sink the fleet
            return BatchResult(item.key, item.problem, None, error=f"{type(exc).__name__}: {exc}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(work, i, it): i for i, it in enumerate(items)}
        for fut in concurrent.futures.as_completed(futs):
            i = futs[fut]
            results[i] = fut.result()
            if on_done is not None:
                on_done(results[i])

    return [r for r in results if r is not None]


@dataclass
class BatchReport:
    """Aggregate view of a batch run."""

    results: list[BatchResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def halted(self) -> int:
        return sum(1 for r in self.results if r.halted)

    @property
    def validated(self) -> int:
        return sum(1 for r in self.results if r.trace is not None and r.trace.stop_reason == "validated")

    @property
    def errored(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    def summary(self) -> str:
        return (
            f"batch: {self.total} items | {self.validated} validated (rung<=2) | "
            f"{self.halted} halted | {self.errored} errored"
        )
