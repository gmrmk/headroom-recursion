# headroom-recursion

**Recursive reasoning for Claude — a tiny idea that punches far above its weight.**

This is a Claude Agent Skill plus a runnable harness that ports the principle of
[*"Less is More: Recursive Reasoning with Tiny Networks"*](https://arxiv.org/abs/2510.04871)
(arXiv:2510.04871) to the Claude model family.

The paper shows a **2-layer, 7M-parameter** network that **recursively refines** a draft
answer, beating models tens of thousands of times larger on hard reasoning (ARC-AGI,
Sudoku, Maze). The transferable lesson has two halves, and this harness applies both:

1. **Recursive refinement** — don't answer once. Draft, then loop: refine a reasoning
   *scratchpad*, rewrite the answer from it, and repeat.
2. **Less is more** — a small model looping beats a big model answering once, so start
   cheap and only escalate to a larger model when the small one plateaus.

[Headroom](https://github.com/headroomlabs-ai/headroom) compresses the growing
scratchpad and history before every call, so all that looping stays affordable.
An optional [LightRAG](https://github.com/HKUDS/LightRAG) retrieval layer grounds each
step in a knowledge base for fact-heavy tasks.

## How the paper maps onto Claude

TRM's forward pass — `n` latent updates, then one answer update, repeated:

```python
def deep_recursion(x, y, z, n=6, T=3):   # y = answer, z = latent reasoning
    for j in range(T-1):
        for i in range(n):
            z = net(x, y, z)   # refine reasoning from problem + answer + prior reasoning
        y = net(y, z)          # rewrite answer from reasoning
    ...                        # + a halt (Q-head) prediction: "have I solved it?"
```

| TRM | headroom-recursion |
|---|---|
| `x` problem | the task prompt |
| `y` answer | current candidate answer (text) |
| `z` latent reasoning | a reasoning **scratchpad** (critique/notes) |
| `z = net(x,y,z)` ×`n` | `n` cheap-model calls that critique the answer and update the scratchpad |
| `y = net(y,z)` | one call that rewrites the answer from the scratchpad |
| deep supervision (carry `y,z` forward) | improvement-step loop, `(y,z)` threaded between steps |
| `Q_head` halt prediction | a strict self-eval judge → `halt_prob ∈ [0,1]` (+ convergence check) |
| "tiny net beats big net" | **tier ladder**: recurse on Haiku first; escalate only on non-halt |
| — | **Headroom** compresses context each call so long recursion stays cheap |

The model ladder, cheapest → most capable:
`claude-haiku-4-5-20251001` → `claude-sonnet-5` → `claude-opus-4-8` → `claude-fable-5`.
State carries *up* the ladder — a bigger model continues from the best draft rather than
restarting.

## Install

```bash
pip install -e '.[headroom,dev]'   # headroom = optional headroom-ai; dev = pytest
export ANTHROPIC_API_KEY=sk-ant-...
```

`headroom-ai` is optional: without it the loop still runs (uncompressed) and reports
zero token savings.

## Use

Command line:

```bash
recurse "A 4×4 grid must be a Latin square using 1–4; row1 = [_, 3, _, _] ..."
recurse --dry-run "..."                 # print the call schedule, no API calls
recurse --no-headroom --json "..."      # disable compression, emit the full JSON trace
recurse --n 4 --steps 2 --threshold 0.85 "..."
```

Library:

```python
from headroom_recursion import recurse, RecurseConfig
from headroom_recursion.claude import ClaudeClient

trace = recurse(
    "…hard problem…",
    client=ClaudeClient(),
    config=RecurseConfig(n=6, T=3, halt_threshold=0.9),
)
print(trace.summary())          # answer + tier path + Headroom savings
```

Structured tasks can pass a `validator` — an oracle that returns `True` when the answer
is provably correct (e.g. a solved grid). It halts the loop immediately, no judge call.

Knowledge-heavy tasks can pass a `retriever`. LightRAG (Claude-backed) grounds each
step in a knowledge base:

```bash
pip install -e '.[lightrag]'
recurse --lightrag ./kb --index corpus.txt "What does the corpus say about X?"
```

See `references/lightrag-setup.md`. Retrieval is pluggable — any object with
`retrieve(query, *, k) -> list[str]` works.

## Test

```bash
pytest        # network-free: a stub Claude client exercises the whole loop
```

See `references/paper-mapping.md` for the full rationale and `references/headroom-setup.md`
for library / proxy / MCP integration modes.

## Layout

```
SKILL.md                       # the Claude Agent Skill
src/headroom_recursion/
  config.py    trm.py          # config + the core recursion loop
  ladder.py    halting.py      # tier escalation + the halt predictor
  headroom.py  claude.py       # Headroom compression + the Claude wrapper
  retrieval.py                 # optional LightRAG retrieval layer (Claude-backed)
  prompts.py   trace.py  cli.py
references/  examples/  tests/
```

## License

MIT.
