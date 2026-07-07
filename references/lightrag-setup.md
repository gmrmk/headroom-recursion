# LightRAG retrieval layer

By default the recursion reasons from the problem statement alone. For knowledge-heavy
tasks (research, OSINT, docs Q&A, anything needing *facts* rather than only careful
logic), plug in [LightRAG](https://github.com/HKUDS/LightRAG) as a retrieval layer:
each improvement step pulls relevant snippets from a knowledge base and injects them
into the scratchpad-refinement and answer prompts. That retrieved context then flows
through Headroom compression along with everything else, so grounding the recursion
doesn't blow the token budget.

Retrieval is **optional and pluggable** — any object with
`retrieve(query, *, k) -> list[str]` works. LightRAG is one backend.

## Install

```bash
pip install -e '.[lightrag]'        # pulls lightrag-hku
```

LightRAG also needs an **embedding model**, which Claude does not provide. Choose one:

- **OpenAI** embeddings (`text-embedding-3-small`, dim 1536),
- a **local** model via sentence-transformers / HF (offline),
- the bundled **`simple_local_embedding`** — dependency-free hashing embeddings, fine for
  wiring/tests, *not* for production retrieval quality.

Claude is wired in as LightRAG's LLM automatically (`build_claude_llm_func`).

## Python

```python
from headroom_recursion import recurse, RecurseConfig, LightRAGRetriever, simple_local_embedding
from headroom_recursion.claude import ClaudeClient

client = ClaudeClient()

retriever = LightRAGRetriever(
    working_dir="./kb",                 # LightRAG persists its graph/vectors here
    client=client,                       # Claude backs LightRAG's LLM (entity extraction, etc.)
    llm_model="claude-sonnet-5",         # model for LightRAG's internal LLM calls
    embedding=simple_local_embedding(),  # swap for a real embedding backend in production
    mode="mix",                          # local | global | hybrid | naive | mix
)
retriever.index(open("corpus.txt").read())   # build the store (one-time; skip if working_dir is prebuilt)

trace = recurse(
    "According to the corpus, what caused the 1911 outage?",
    client=client,
    config=RecurseConfig(retriever=retriever, retrieval_k=4),
)
print(trace.summary())   # includes a "retrieval: N snippets injected" line
```

### A real embedding backend

```python
from headroom_recursion import Embedding
import openai

async def _embed(texts):
    resp = await openai.AsyncOpenAI().embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in resp.data]

embedding = Embedding(func=_embed, dim=1536, max_token_size=8192)
```

## CLI

```bash
# Index two files into ./kb, then answer a grounded question.
recurse --lightrag ./kb --index notes.txt --index spec.txt \
        --lightrag-mode mix --retrieval-k 4 \
        "What does the spec say about retries?"

# Reuse an already-built store (no --index).
recurse --lightrag ./kb "Summarize the retry policy and cite it."
```

## How it fits the loop

Per improvement step, before the `n` scratchpad refinements:

1. Form a query from the problem plus the current scratchpad (truncated to
   `retrieval_query_chars`).
2. `retriever.retrieve(query, k=retrieval_k)` returns snippets.
3. They are rendered into a `RETRIEVED KNOWLEDGE:` block (`prompts.format_context`) and
   injected into the latent-update and answer-update prompts.
4. The prompts (retrieved context included) pass through Headroom before hitting Claude.

Because retrieval runs **each step**, the query sharpens as the scratchpad evolves — the
recursion pulls *different* evidence as its understanding of the problem improves.
Retrieval failures are swallowed (the loop continues ungrounded) so a flaky index never
breaks reasoning.
