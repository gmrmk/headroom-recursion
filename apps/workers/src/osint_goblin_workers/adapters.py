"""Adapter registry for the single tool_runner actor.

Diego sec.B2 + Sora ADR-0004: ONE Dramatiq actor (tool_runner) dispatches to
N adapters via a name->callable registry. New tools land as registry entries,
not new actors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AdapterCallable(Protocol):
    """Adapter contract: receives a dict payload, returns a list of event dicts
    (which the tool_runner emits via SSE)."""

    def __call__(self, payload: dict) -> list[dict]: ...


@dataclass(frozen=True, slots=True)
class AdapterEntry:
    """A single registered adapter."""

    id: str
    callable: AdapterCallable
    in_process: bool = True  # False -> subprocess wrapper (AGPL containment)
    description: str = ""


class AdapterRegistry:
    """Mutable registry. Singleton-ish at import time (see _REGISTRY below)."""

    def __init__(self) -> None:
        self._entries: dict[str, AdapterEntry] = {}

    def register(
        self,
        adapter_id: str,
        callable_: AdapterCallable,
        *,
        in_process: bool = True,
        description: str = "",
    ) -> AdapterEntry:
        if adapter_id in self._entries:
            raise ValueError(f"adapter {adapter_id!r} already registered")
        entry = AdapterEntry(
            id=adapter_id,
            callable=callable_,
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


# Module-level registry. Imported by tool_runner.py.
_REGISTRY = AdapterRegistry()


def get_registry() -> AdapterRegistry:
    """The shared registry. Test seam: callers may replace _REGISTRY for isolation
    (or use the AdapterRegistry class directly with manual injection)."""
    return _REGISTRY


# Day 8: register a single 'echo' adapter for smoke testing. Maigret + the
# other 11 M1 adapters land Day 9+ (WI-0205).
def _echo(payload: dict) -> list[dict]:
    """Trivial adapter: echoes the payload back as a single event."""
    return [{"event_type": "tool-run-result", "payload": payload}]


_REGISTRY.register("echo", _echo, in_process=True, description="Day-8 smoke adapter.")
