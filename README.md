# headroom-recursion

Solve hard reasoning problems by refining in a loop instead of answering once. A cheap
Claude model drafts, critiques, and rewrites its answer; larger models take over only
when it stops making progress.

This is a Claude Agent Skill plus a runnable harness based on
[*"Less is More: Recursive Reasoning with Tiny Networks"*](https://arxiv.org/abs/2510.04871)
(arXiv:2510.04871), which shows a 2-layer, 7M-parameter network that recursively
refines a draft answer outperforming much larger models on hard reasoning benchmarks
(ARC-AGI, Sudoku, Maze). Two ideas from the paper carry over to LLMs:

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

The default model ladder, cheapest → most capable:
`claude-haiku-4-5-20251001` → `claude-sonnet-5` → `claude-opus-4-8` → `claude-fable-5`.
State carries *up* the ladder — a bigger model continues from the best draft rather than
restarting.

## Other model providers

The loop is model-agnostic: it talks to a backend through one method
(`clients.CompletionClient` — `complete(model, system, user, ...) -> CallResult`), and
model names in the ladder are opaque strings. Two backends ship:

- `ClaudeClient` (default) — the Anthropic SDK.
- `OpenAIClient` — the OpenAI SDK, which also covers any OpenAI-compatible server
  (Ollama, vLLM, LM Studio, OpenRouter, ...) via `base_url`.

```bash
pip install -e '.[openai]'
recurse --client openai --ladder "gpt-4o-mini,gpt-4o" "…"
recurse --client openai --base-url http://localhost:11434/v1 \
        --ladder "llama3.2:3b,llama3.3:70b" "…"        # local Ollama
```

Anything else (a raw HTTP endpoint, a CLI, a test stub) just needs an object with that
one `complete` method — pass it as `recurse(..., client=your_client)`.

## Install

```bash
pip install -e '.[headroom,dev]'   # headroom = optional headroom-ai; dev = pytest
export ANTHROPIC_API_KEY=sk-ant-...
```

`headroom-ai` is optional: without it the loop still runs (uncompressed) and reports
zero token savings. When a run needs something that isn't installed, the CLI offers to
install it (interactive sessions only — scripts get an error with the pip command).

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

## Provable results: the oracle compiler

`--auto-oracle` makes step zero of a run *synthesize its own verifier*: a model
writes a mechanical checker for the problem, the checker is calibrated against
planted good/bad cases in a sandbox, and only if it discriminates them all does
it gain halt authority — otherwise the judge keeps full control. Residuals
(what the checker can't verify) are printed in the trace and handed to the
judge. Research-mode extras: `--claim-audit` resolves `[KNOWN]` citations and
hunts prior art for `[NEW]` claims via the retriever; `--ledger` carries
verified results across runs; validators can return provisional `Verdict`s with
a settlement date for claims only the future can grade. Any outcome resting on
judged opinion at ≥ 0.40 is flagged `NEEDS HUMAN REVIEW`. Full doctrine and the
live-run evidence behind it: `references/oracle-compiler.md`.

Halt authority matches declared coverage: a compiled validator must claim
`sufficient` explicitly, or it runs as a **gate** — its rejections are final
(and skip the judge, saving the run's dominant cost), but its passes defer to
the judge and never halt as "validated". For research work, `--research` wraps
the problem in the graded-rubric template proven across the live P-vs-NP runs
and applies the doctrine those runs earned as defaults: a Sonnet+ ladder, the
judge pinned to Opus, and 3-vote median scoring (explicit flags still win);
`--corpus bibliography.txt` powers the citation firewall with a curated file
(see `examples/complexity-bibliography.txt`) via `CorpusRetriever` — no
LightRAG required.

Rung 1 runs live via Lean 4: `--lean-gate` mechanically rejects any answer
whose ```lean blocks fail to compile (compiler errors are fed back into the
next step), while passes defer to the judge — compiling is not the same as
proving the right thing. `--lean-statement FILE` upgrades to a **decider**: a
trusted skeleton pins the theorem statement, the model contributes only the
proof, and a validated halt requires a kernel-checked compile plus a
`#print axioms` audit against Lean's three standard axioms (failing closed).
`--trace-dir runs` persists every run's trace, summary, and decider artifacts
so any claim is auditable offline; `lean/` is the pinned Mathlib project and
`scripts/install_lean.sh` installs the toolchain (building from source where
egress policy blocks release binaries).

## Independent verification

Compiling a proof on the toolchain we built ourselves is only the first tier of
trust. `scripts/verify_artifacts.py` re-checks every persisted Lean artifact
*independently*:

1. **Kernel replay** — `leanchecker` (shipped inside Lean ≥ 4.28) re-runs each
   proof's declarations through the kernel, catching elaborator/metaprogram
   circumvention. This is Lean's *own* kernel, so it does not reduce trust in
   the kernel itself — only in the elaboration around it. Self-contained
   artifacts get a full `--fresh` replay of their transitive environment;
   `import Mathlib` artifacts get an incremental replay of the module (trusting
   the prebuilt Mathlib environment), with their used Mathlib dependencies
   re-checked instead by tier 2's export closure. `runs/verify/report.json`
   records which mode ran per artifact.
2. **Second, independent kernel** — the proof is serialized with
   [`lean4export`](https://github.com/leanprover/lean4export) (v4.31.0) to Lean's
   NDJSON export format and re-typechecked by
   [`nanoda`](https://github.com/ammkrn/nanoda_lib), a from-scratch Rust
   reimplementation of the Lean kernel, with the axiom set audited against
   `{propext, Classical.choice, Quot.sound}`. This is the *only*
   implementation-independent tier — and the export file is published, so a
   third party can run it without trusting our toolchain.

`runs/verify/report.json` records the outcome and checker versions. What this
buys, stated honestly: a *sound* proof, replayed by Lean's own kernel and
re-checked by one genuinely independent kernel implementation. It does **not**
buy novelty — see below.

## Case study: the P vs NP campaign

`runs/FINDINGS.md` is the live-run writeup of pointing this whole stack at
"Resolve P vs NP" — chosen not because we expected to solve it, but because a
genuinely open problem is the one benchmark that **cannot be faked**: any credit
the system awards must be manufactured by its own verification machinery, and a
"validated" halt would by construction be a bug, not a discovery.

The honest result: **no novel mathematics.** Across every configuration, the
best judged score was 0.30 (all-`[KNOWN]` restatements; the rubric caps
fabrication at 0.05 and no run cleared it). The Lean artifacts that *were*
independently verified — a Shannon-style counting argument, and
hypothesis-conditional shells that *assume* (rather than establish) the
relativization and Karp–Lipton barriers — are **classical folklore, not
new results**; what the kernels confirm is that they are *sound*, and what the
campaign demonstrates is the *methodology* (verification-gated refinement with
mechanically-audited credit), not progress on the problem. The value here is a
loop that reasons hard and refuses to overclaim — including refusing to let its
own operators call folklore novel.

## Safety rails

A self-refining loop has sharp edges; these are built in:

- **No false halts.** A judge reply is only trusted as a probability if it actually is
  one — "I found 3 errors" is not `halt_prob = 1.0`. Garbage replies get one re-ask,
  then count as "don't halt". `--judge-votes 3` takes the median of independent votes
  (robust to one sycophantic self-grade); `--judge-model` pins the verifier to a
  different model. The judge's input is never Headroom-compressed by default
  (`compress_judge`) — it verifies the exact answer text, not a paraphrase.
- **State can't be destroyed.** Empty/whitespace completions never replace the
  scratchpad or answer (counted as `rejected_updates` in the trace), and calls cut off
  at `max_tokens` are flagged `truncated`.
- **The best answer wins.** Refinement isn't monotone; on any non-confident exit the
  run returns the highest-scoring answer seen (`best_*` in the trace), not the latest.
- **Oscillation is convergence.** An A→B→A answer cycle escalates instead of burning
  the tier's whole step budget.
- **Hard budgets.** `--max-calls` / `--max-seconds` stop the run at a step boundary
  with the best answer so far — and never escalate to a pricier tier after the budget
  is gone. (LightRAG's internal LLM calls are not counted; see
  `references/lightrag-setup.md`.)
- **Work is never lost.** Ctrl-C returns the partial trace (exit 130); an API error
  raises `RunError` whose `.trace` holds everything completed (CLI prints it, exit 1).
  `RecurseConfig.validate()` rejects silently-broken configs (`n=0`, `threshold=1.5`)
  before the first call.

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
CREDITS.md                     # attribution — everyone this work stands on
src/headroom_recursion/
  config.py    trm.py          # config + the core recursion loop
  ladder.py    halting.py      # tier escalation + the halt predictor
  headroom.py  claude.py       # Headroom compression + the Claude wrapper
  clients.py                   # CompletionClient protocol + OpenAI/CLI backends
  oracle.py    lean_oracle.py  # oracle compiler + rung-1 Lean gate/decider
  claims.py    ledger.py       # citation firewall + cross-run ledger
  campaign.py  heartbeat.py    # multi-run goal loop + per-call telemetry
  doctor.py                    # readiness check (deps, transport, Lean levels)
  retrieval.py                 # optional LightRAG retrieval layer (Claude-backed)
  prompts.py   trace.py  cli.py
lean/                          # pinned Lean 4 + Mathlib project (rung-1 backend)
scripts/                       # install_lean.sh, verify_artifacts.py
runs/                          # persisted evidence: traces, verification, FINDINGS.md
references/  examples/  tests/
```

## License

MIT — see `CREDITS.md` for the work this builds on and the attribution that
license (and honesty) asks you to preserve.
