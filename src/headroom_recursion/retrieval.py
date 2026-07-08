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
import threading
import warnings
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


class MultiRetriever:
    """Interleave results from several retrievers (order-preserving dedupe).

    Lets a run ground its reasoning in BOTH a curated corpus (exact, auditable)
    and a knowledge base (broad, fuzzy). Note the firewall rule: claim AUDITS
    should keep an exact retriever (``RecurseConfig.audit_retriever``) — a
    fuzzy backend that returns loosely-related context for any query would
    "resolve" fabricated citations.
    """

    def __init__(self, *retrievers):
        self._retrievers = [r for r in retrievers if r is not None]

    def retrieve(self, query: str, *, k: int) -> list[str]:
        rounds = [r.retrieve(query, k=k) or [] for r in self._retrievers]
        out: list[str] = []
        seen: set[str] = set()
        for i in range(max((len(r) for r in rounds), default=0)):
            for hits in rounds:
                if i < len(hits) and hits[i] and hits[i].strip() and hits[i] not in seen:
                    seen.add(hits[i])
                    out.append(hits[i])
                    if len(out) >= k:
                        return out
        return out


class CorpusRetriever:
    """Keyword-overlap retrieval over a small curated corpus — a rung-4 source
    without LightRAG.

    This is the retriever that powered the citation firewall in the live
    research runs: `entries` is a list of strings (e.g. one bibliography entry
    per line: author names, year, venue, one-line result). A query matches an
    entry when they share at least ``min_overlap`` alphanumeric tokens; entries
    are returned best-overlap-first. Deliberately dumb and auditable — for a
    real knowledge base use ``LightRAGRetriever``.
    """

    def __init__(self, entries: list[str], *, min_overlap: int = 2):
        self._entries = [e.strip() for e in entries if e and e.strip() and not e.lstrip().startswith("#")]
        self._min_overlap = min_overlap
        self._tokens = [set(re.findall(r"[a-z0-9]+", e.lower())) for e in self._entries]

    @classmethod
    def from_file(cls, path: str, **kw) -> "CorpusRetriever":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(fh.readlines(), **kw)

    def retrieve(self, query: str, *, k: int) -> list[str]:
        qtok = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = [
            (len(qtok & etok), entry)
            for entry, etok in zip(self._entries, self._tokens)
            if len(qtok & etok) >= self._min_overlap
        ]
        scored.sort(key=lambda t: -t[0])
        return [e for _, e in scored[:k]]


def _run_coro(coro: Awaitable):
    """Run a self-contained coroutine to completion, loop or no loop.

    Each call gets a FRESH event loop, so this must never be used for objects that
    hold loop-bound state across calls (locks, sessions, pipelines) — LightRAG does;
    it goes through ``_LoopRunner`` instead.
    """

    if not inspect.isawaitable(coro):
        return coro
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Inside a running loop: execute on a fresh loop in a worker thread.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result(timeout=300)


class _LoopRunner:
    """One persistent background event loop for all of an object's coroutines.

    LightRAG initializes asyncio locks, pipeline status, and storage sessions bound
    to the loop they were created on. Running each call on a throwaway loop (the old
    behavior) either raises "attached to a different event loop" or deadlocks on a
    lock whose loop is dead. This runner keeps a single daemon loop alive so every
    coroutine sees the same loop from ``initialize_storages`` to the last ``aquery``.
    """

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def run(self, coro: Awaitable, timeout: float = 300):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


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

    async def embed(texts: list[str]):
        # numpy ships with LightRAG (whose newer builds require array output —
        # they call .size on it); this helper only runs alongside LightRAG.
        import numpy as np

        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * dim
            for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                vec[h % dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return np.asarray(out, dtype=np.float32)

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

        if embedding is None:
            warnings.warn(
                "LightRAGRetriever: no embedding supplied — falling back to "
                "simple_local_embedding (hashing bag-of-words, LOW retrieval quality). "
                "Supply a real Embedding (OpenAI, sentence-transformers, ...) for "
                "production retrieval.",
                stacklevel=2,
            )
        emb = embedding or simple_local_embedding()

        self._QueryParam = QueryParam
        self._mode = mode
        self._runner = _LoopRunner()
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
        # Everything runs on the retriever's single persistent loop — LightRAG's
        # loop-bound state (locks, sessions) must live and die on one loop.
        self._runner.run(self._rag.initialize_storages())
        # API drift: older builds expose initialize_pipeline_status as a method,
        # newer ones as a module-level function in kg.shared_storage.
        init_pipeline = getattr(self._rag, "initialize_pipeline_status", None)
        if init_pipeline is None:
            try:
                from lightrag.kg.shared_storage import (  # type: ignore
                    initialize_pipeline_status as init_pipeline,
                )
            except Exception:
                init_pipeline = None
        if init_pipeline is not None:
            self._runner.run(init_pipeline())
        self._initialized = True

    def index(self, docs) -> None:
        """Insert one document (str) or many (iterable of str) into the store."""

        self._ensure_init()
        if isinstance(docs, str):
            docs = [docs]
        for doc in docs:
            self._runner.run(self._rag.ainsert(doc))

    def retrieve(self, query: str, *, k: int) -> list[str]:
        self._ensure_init()
        param = self._QueryParam(mode=self._mode, top_k=k, only_need_context=True)
        context = self._runner.run(self._rag.aquery(query, param=param))
        if not context or not str(context).strip():
            return []
        return [str(context).strip()]

    def close(self) -> None:
        """Stop the background event loop (idempotent enough for atexit use)."""

        try:
            self._runner.close()
        except Exception:
            pass
