"""headroom-recursion — TRM-style recursive reasoning for Claude.

Ports the principle of "Less is More: Recursive Reasoning with Tiny Networks"
(arXiv:2510.04871) to the Claude model family: a cheap model recursively refines a
reasoning scratchpad and candidate answer, a self-evaluation acts as the halt
predictor, and the loop escalates up the model ladder only when a tier plateaus.
Headroom compresses the growing context before each call so long recursion stays
affordable.
"""

from headroom_recursion.config import RecurseConfig, Tier, DEFAULT_LADDER, RESEARCH_LADDER
from headroom_recursion.trace import RunTrace, StepTrace
from headroom_recursion.ladder import RunError, recurse
from headroom_recursion.clients import CompletionClient, OpenAIClient, CLITransportClient
from headroom_recursion.oracle import CompiledOracle, compile_oracle
from headroom_recursion.prompts import research_prompt
from headroom_recursion.retrieval import (
    Retriever,
    NullRetriever,
    LightRAGRetriever,
    CorpusRetriever,
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
    "CompletionClient",
    "OpenAIClient",
    "CLITransportClient",
    "RESEARCH_LADDER",
    "CompiledOracle",
    "compile_oracle",
    "research_prompt",
    "CorpusRetriever",
    "Retriever",
    "NullRetriever",
    "LightRAGRetriever",
    "Embedding",
    "simple_local_embedding",
    "build_claude_llm_func",
]

__version__ = "0.1.0"
