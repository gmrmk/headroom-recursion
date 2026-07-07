"""Optional retrieval layer — ground the recursion in a knowledge base.

The core loop reasons from the problem text alone. For knowledge-heavy tasks that is
not enough: the model needs *facts*, not just careful reasoning. This module lets each
improvement step pull relevant snippets from a knowledge base and inject them into the
scratchpad-refinement prompt, so the recursion reasons over retrieved evidence.

The reference backend is [LightRAG](https://github.com/HKUDS/LightRAG) (graph + vector
RAG), driven by Claude as its LLM. Retrieval is entirely optional: with no retriever
configured the loop behaves exactly as before.

Design notes:
* Any object with ``retrieve(query, *, k) -> list[str]`` is a valid retriever (see the
  ``Retriever`` protocol) — tests use a trivial stub, no LightRAG needed.
* LightRAG is async-first; ``LightRAGRetriever`` runs its coroutines behind a sync
  ``retrieve``/``index`` so it drops into the (synchronous) recursion loop unchanged.
* LightRAG needs an embedding model, which Claude does not provide. Supply your own via
  ``Embedding`` (OpenAI, a local sentence-transformers model, …). A dependency-free
  ``simple_local_embedding`` is included for offline/dev use — low quality, fine for
  wiring and tests, not for production retrieval.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import math
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Protocol, runtime_checkable


@runtime_checkable
class Retriever(Protocol):
    def retrieve(self, query: str, *, k: int) -> list[str]:
        """Return up to ``k`` relevant text snippets for ``query`` (possibly fewer)."""
        ...


class NullRetriever:
    """A retriever that retrieves nothing (the default)."""

    def retrieve(self, query: str, *, k: int) -> list[str]:  # noqa: D401
        return []


def _run_coro(coro: Awaitable):
    """Run a coroutine to completion whether or not a loop is already running."""

    if not inspect.isawaitable(coro):
        return coro
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Inside a running loop: execute on a fresh loop in a worker thread.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


# --------------------------------------------------------------------------------------
# Claude as LightRAG's LLM
# --------------------------------------------------------------------------------------

def build_claude_llm_func(client, model: str) -> Callable[..., Awaitable[str]]:
    """Adapt a ``ClaudeClient`` into LightRAG's ``llm_model_func`` (async) signature."""

    async def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs) -> str:
        def call() -> str:
            return client.complete(
                model=model,
                system=system_prompt or "You are a precise assistant.",
                user=prompt,
                max_tokens=kwargs.get("max_tokens", 2048),
                temperature=0.0,
                use_headroom=False,  # LightRAG's internal prompts are short; compress at the loop layer
            ).text

        return await asyncio.to_thread(call)

    return llm_model_func


# --------------------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------------------

@dataclass
class Embedding:
    """An embedding backend for LightRAG: an async ``func`` plus its metadata."""

    func: Callable[[list[str]], Awaitable[list[list[float]]]]
    dim: int
    max_token_size: int = 8192


def simple_local_embedding(dim: int = 256, max_token_size: int = 8192) -> Embedding:
    """A dependency-free hashing embedding — deterministic, offline, LOW quality.

    Hashes tokens into a fixed-width bag-of-words vector and L2-normalizes. Good enough
    to wire up and test LightRAG end-to-end without an external embedding provider;
    replace it with a real model (OpenAI, sentence-transformers) for real retrieval.
    """

    async def embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * dim
            for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                vec[h % dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out

    return Embedding(func=embed, dim=dim, max_token_size=max_token_size)


# --------------------------------------------------------------------------------------
# LightRAG-backed retriever
# --------------------------------------------------------------------------------------

class LightRAGRetriever:
    """A ``Retriever`` backed by LightRAG, using Claude as the LLM.

    ``lightrag-hku`` is an optional dependency; importing this class without it raises a
    clear error. Construct once, ``index(...)`` your documents (or point ``working_dir``
    at an already-built store), then pass the instance as ``RecurseConfig.retriever``.
    """

    def __init__(
        self,
        working_dir: str,
        *,
        client=None,
        llm_model: str = "claude-sonnet-5",
        embedding: Optional[Embedding] = None,
        mode: str = "mix",
    ):
        try:
            from lightrag import LightRAG, QueryParam  # type: ignore
            from lightrag.utils import EmbeddingFunc  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "LightRAGRetriever requires the 'lightrag' extra: pip install "
                "'headroom-recursion[lightrag]' (package lightrag-hku)."
            ) from exc

        if client is None:
            from headroom_recursion.claude import ClaudeClient

            client = ClaudeClient()

        emb = embedding or simple_local_embedding()

        self._QueryParam = QueryParam
        self._mode = mode
        self._rag = LightRAG(
            working_dir=working_dir,
            llm_model_func=build_claude_llm_func(client, llm_model),
            embedding_func=EmbeddingFunc(
                embedding_dim=emb.dim, max_token_size=emb.max_token_size, func=emb.func
            ),
        )
        self._initialized = False

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        _run_coro(self._rag.initialize_storages())
        _run_coro(self._rag.initialize_pipeline_status())
        self._initialized = True

    def index(self, docs) -> None:
        """Insert one document (str) or many (iterable of str) into the store."""

        self._ensure_init()
        if isinstance(docs, str):
            docs = [docs]
        for doc in docs:
            _run_coro(self._rag.ainsert(doc))

    def retrieve(self, query: str, *, k: int) -> list[str]:
        self._ensure_init()
        param = self._QueryParam(mode=self._mode, top_k=k, only_need_context=True)
        context = _run_coro(self._rag.aquery(query, param=param))
        if not context or not str(context).strip():
            return []
        return [str(context).strip()]
