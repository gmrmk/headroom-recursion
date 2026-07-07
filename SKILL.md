---
name: headroom-recursion
description: >-
  Solve a hard reasoning task by recursive refinement across Claude model tiers
  instead of answering in one shot. Use when a problem is easy to state but easy to
  get wrong — logic/constraint puzzles (Sudoku, Latin squares, scheduling, graph
  coloring), multi-step math or proofs, tricky code/algorithm derivations, or any
  task where a first-pass answer is unreliable and the user says "think harder,"
  "be careful," or "check your work." Runs a cheap model (Haiku) in a draft →
  critique → rewrite loop, escalating to Sonnet/Opus/Fable only when it plateaus,
  with Headroom compressing context so the looping stays cheap. Skip for simple
  lookups, chit-chat, or anything a single call already answers well.
---

# headroom-recursion

Port of *"Less is More: Recursive Reasoning with Tiny Networks"* (arXiv:2510.04871)
to Claude. A small model that **recursively refines** a draft answer can beat a large
model that answers once. This skill runs that loop for you.

## When to use

Reach for it when one-shot answering is unreliable:
- constraint/logic puzzles, combinatorial search, scheduling, graph problems;
- multi-step arithmetic/algebra/proofs where a slip compounds;
- algorithm or code derivations with an easy-to-check result;
- any request to "double-check", "reason carefully", or "don't rush".

Do **not** use it for factual lookups, summaries, casual conversation, or tasks a
single Claude call handles well — the loop spends many calls and is slower.

## How it works

Each **improvement step** does `n` scratchpad refinements (`z = net(x,y,z)`) then one
answer rewrite (`y = net(y,z)`), carrying `(answer, scratchpad)` forward. After each
step a strict verifier predicts `halt_prob` (the paper's Q-head); the loop stops when
it is confident, when the answer converges, or when a validator oracle confirms it.
It starts on the cheapest model and **escalates up the ladder only on non-halt** —
Haiku → Sonnet → Opus → Fable — carrying the best draft upward. Headroom compresses
the context before every call.

## How to run it

Prereq once: `pip install -e '.[headroom]'` and `export ANTHROPIC_API_KEY=…`.

```bash
recurse "<the full problem statement>"        # prints answer + tier path + savings
recurse --dry-run "<problem>"                  # show the call schedule, no API calls
recurse --json "<problem>"                     # full structured trace
recurse --n 4 --steps 2 --threshold 0.85 "…"   # tune recursion depth / halt bar
```

Or from Python — see `README.md` (`from headroom_recursion import recurse`). For
structured answers, pass a `validator` in `RecurseConfig` to halt on a verified
solution with zero judge calls.

For knowledge-heavy tasks, ground the recursion in a corpus with the optional
LightRAG retrieval layer (each step retrieves and injects relevant snippets):

```bash
pip install -e '.[lightrag]'
recurse --lightrag ./kb --index corpus.txt "<question over the corpus>"
```

See `references/lightrag-setup.md`.

Tune-ables: `--n` (latent updates/step, default 6), `--steps` (steps/tier, default 3),
`--threshold` (halt bar, default 0.9), `--judge-model` (pin the verifier, e.g. to
Haiku), `--no-headroom` (A/B the token savings).

## More detail

- `references/paper-mapping.md` — the full TRM → Claude mapping and rationale.
- `references/headroom-setup.md` — Headroom library / proxy / MCP integration modes.
- `references/lightrag-setup.md` — the optional LightRAG retrieval layer.
- `examples/run_sudoku.md` — a worked run and the shape of its trace.
