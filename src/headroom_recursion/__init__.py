"""headroom-recursion — TRM-style recursive reasoning for Claude.

Ports the principle of "Less is More: Recursive Reasoning with Tiny Networks"
(arXiv:2510.04871) to the Claude model family: a cheap model recursively refines a
reasoning scratchpad and candidate answer, a self-evaluation acts as the halt
predictor, and the loop escalates up the model ladder only when a tier plateaus.
Headroom compresses the growing context before each call so long recursion stays
affordable.
"""

from headroom_recursion.config import RecurseConfig, Tier, DEFAULT_LADDER
from headroom_recursion.trace import RunTrace, StepTrace
from headroom_recursion.ladder import RunError, recurse
from headroom_recursion.retrieval import (
    Retriever,
    NullRetriever,
    LightRAGRetriever,
    Embedding,
    simple_local_embedding,
    build_claude_llm_func,
)

__all__ = [
    "RecurseConfig",
    "Tier",
    "DEFAULT_LADDER",
    "RunTrace",
    "StepTrace",
    "recurse",
    "RunError",
    "Retriever",
    "NullRetriever",
    "LightRAGRetriever",
    "Embedding",
    "simple_local_embedding",
    "build_claude_llm_func",
]

__version__ = "0.1.0"
