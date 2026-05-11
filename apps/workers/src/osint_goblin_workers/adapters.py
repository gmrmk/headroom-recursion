"""Adapter registry for the single tool_runner actor.

Diego sec.B2 + Sora ADR-0004: ONE Dramatiq actor (tool_runner) dispatches to
N adapters via a name->callable registry. New tools land as registry entries,
not new actors.

Yuki P1 (phase6 2026-05-11): every AdapterEntry MUST expose a `synthetic_mode`
callable that produces a contract-compliant event stream (started + >=1 hit +
complete) WITHOUT invoking the live tool. This lets the M0 exit gate exercise
the pipeline (fan-out + queue + chain + stream) without the deployment burden
of provisioning 11 OSINT tools on every CI runner. The mandate is enforced at
register-time (no synthetic_mode -> registration raises) so missing-fixture
regressions surface immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .subprocess_adapter import make_subprocess_adapter


class AdapterCallable(Protocol):
    """Adapter contract: receives a dict payload, returns a list of event dicts
    (which the tool_runner emits via SSE)."""

    def __call__(self, payload: dict) -> list[dict]: ...


@dataclass(frozen=True, slots=True)
class AdapterEntry:
    """A single registered adapter.

    `callable` is the live-tool path; `synthetic_mode` is the deterministic
    no-network path used by the M0 exit gate. For pure in-process adapters
    that never touch the network (e.g. `echo`), the two may legitimately be
    the same function -- the contract is "synthetic_mode emits the wire shape
    without external dependencies," which the in-process callable trivially
    satisfies.
    """

    id: str
    callable: AdapterCallable
    synthetic_mode: AdapterCallable
    in_process: bool = True  # False -> subprocess wrapper (AGPL containment)
    description: str = ""


class AdapterRegistry:
    """Mutable registry. Singleton-ish at import time (see _REGISTRY below).

    register() requires a `synthetic_mode` callable; passing None raises so
    missing-fixture regressions surface at import time, not at M0 exit gate.
    """

    def __init__(self) -> None:
        self._entries: dict[str, AdapterEntry] = {}

    def register(
        self,
        adapter_id: str,
        callable_: AdapterCallable,
        *,
        synthetic_mode: AdapterCallable,
        in_process: bool = True,
        description: str = "",
    ) -> AdapterEntry:
        if adapter_id in self._entries:
            raise ValueError(f"adapter {adapter_id!r} already registered")
        if synthetic_mode is None:
            raise ValueError(
                f"adapter {adapter_id!r}: synthetic_mode is mandatory (Yuki P1 phase6). "
                f"For in-process adapters that don't touch the network, you may pass "
                f"the same function as synthetic_mode=callable_."
            )
        entry = AdapterEntry(
            id=adapter_id,
            callable=callable_,
            synthetic_mode=synthetic_mode,
            in_process=in_process,
            description=description,
        )
        self._entries[adapter_id] = entry
        return entry

    def unregister(self, adapter_id: str) -> None:
        self._entries.pop(adapter_id, None)

    def get(self, adapter_id: str) -> AdapterEntry | None:
        return self._entries.get(adapter_id)

    def names(self) -> list[str]:
        return sorted(self._entries.keys())

    def assert_all_have_synthetic_mode(self) -> None:
        """Registry-level lint, callable from CI. Raises if any adapter is
        missing synthetic_mode (defense in depth -- register() also checks)."""
        missing = [eid for eid, e in self._entries.items() if e.synthetic_mode is None]
        if missing:
            raise AssertionError(
                f"adapters missing synthetic_mode (Yuki P1 phase6): {sorted(missing)}"
            )


# Module-level registry. Imported by tool_runner.py.
_REGISTRY = AdapterRegistry()


def get_registry() -> AdapterRegistry:
    """The shared registry. Test seam: callers may replace _REGISTRY for isolation
    (or use the AdapterRegistry class directly with manual injection)."""
    return _REGISTRY


# Day 8: 'echo' smoke adapter for in-process contract.
def _echo(payload: dict) -> list[dict]:
    """Trivial adapter: echoes the payload back as a single event. Pure
    in-process; never touches the network, so the live path IS the synthetic
    path."""
    return [{"event_type": "tool-run-result", "payload": payload}]


_REGISTRY.register(
    "echo",
    _echo,
    synthetic_mode=_echo,  # echo is purely in-process; live == synthetic
    in_process=True,
    description="Day-8 smoke adapter.",
)


# R-6 (Sprint 2 Day 11-12): worker_stress emits N synthetic events from the
# WORKER process so the soak test exercises the actual Redis pub/sub bridge
# (not the in-process m0_gate_stress path which only proves SSE works
# within one process). Default N=32 mirrors m0_gate_stress so the assertion
# floors are the same.
_CYCLE_EVENT_TYPES = (
    "capture-started",
    "warc-written",
    "ed25519-signed",
    "rfc3161-stamped",
    "minio-stored",
    "ftm-entity-created",
    "wayback-queued",
    "tool-run-result",
)


def _worker_stress(payload: dict) -> list[dict]:
    """Emit N events synchronously. The worker actor publishes each to
    Redis pub/sub. N is `count` in the payload, default 32."""
    count = int(payload.get("count", 32))
    return [
        {
            "event_type": _CYCLE_EVENT_TYPES[i % len(_CYCLE_EVENT_TYPES)],
            "payload": {"i": i, "synthetic": True, "source": "worker_stress"},
        }
        for i in range(count)
    ]


_REGISTRY.register(
    "worker_stress",
    _worker_stress,
    synthetic_mode=_worker_stress,  # pure in-process generator; live == synthetic
    in_process=True,
    description="R-6 soak/bridge test adapter. Emits N synthetic events from the worker process.",
)


# Day 9 (WI-0205): Maigret -- first real subprocess-isolated AGPL adapter.
# Path is resolved relative to the repo root at import time.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_MAIGRET_WRAPPER = _REPO_ROOT / "adapters" / "maigret" / "wrapper.py"
if _MAIGRET_WRAPPER.is_file():
    _REGISTRY.register(
        "maigret",
        make_subprocess_adapter(_MAIGRET_WRAPPER, timeout_s=180.0),
        # synthetic_mode forces the wrapper to skip the import-maigret attempt
        # entirely via OSINT_ADAPTER_MODE=synthetic; deterministic event stream.
        synthetic_mode=make_subprocess_adapter(
            _MAIGRET_WRAPPER,
            timeout_s=30.0,
            extra_env={"OSINT_ADAPTER_MODE": "synthetic"},
        ),
        in_process=False,
        description="Maigret username-on-N-sites probe (AGPL-3.0 subprocess-isolated).",
    )
