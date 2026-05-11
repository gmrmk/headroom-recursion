"""osint_goblin_workers -- Dramatiq actors."""

# Side-effect import: registers property-vetting adapters (R-5 Sprint 2)
# in the global registry. Must run before tool_runner is imported so
# that dispatches against the new adapter ids resolve.
from . import (
    adapters_image,  # noqa: F401
    adapters_property,  # noqa: F401
)
from .adapters import AdapterEntry, AdapterRegistry
from .broker import configure_broker, get_broker
from .tool_runner import ToolRunPayload, tool_runner

__all__ = [
    "AdapterEntry",
    "AdapterRegistry",
    "ToolRunPayload",
    "configure_broker",
    "get_broker",
    "tool_runner",
]
