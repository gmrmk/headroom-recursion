# TRM → Claude: the full mapping

Source: **"Less is More: Recursive Reasoning with Tiny Networks"**, Alexia
Jolicoeur-Martineau, [arXiv:2510.04871](https://arxiv.org/abs/2510.04871).

## What the paper does

The Tiny Recursive Model (TRM) is a single **2-layer, ~7M-parameter** network that
solves hard puzzles (ARC-AGI, Sudoku-Extreme, Maze-Hard) by **recursion**, not scale.
It reaches 45% on ARC-AGI-1 and 8% on ARC-AGI-2 — beating Deepseek R1, o3-mini, and
Gemini 2.5 Pro — with **< 0.01%** of their parameters.

It keeps two things across the computation:
- `y` — the current **answer** (embedded),
- `z` — a **latent reasoning** feature (a scratchpad).

The forward pass (paper's Algorithm, lightly reformatted):

```python
def deep_recursion(x, y, z, n=6, T=3):
    with torch.no_grad():                 # earlier improvement steps are carried, not graded
        for j in range(T - 1):
            for i in range(n):            # n latent updates
                z = net(x, y, z)          #   refine reasoning from problem + answer + reasoning
            y = net(y, z)                 # update the answer from the refined reasoning
    for i in range(n):                    # final graded step
        z = net(x, y, z)
    y = net(y, z)
    return (y.detach(), z.detach()), output_head(y), Q_head(y)
```

Key mechanisms:
- **Recursion**: `n = 6` latent updates per step; the answer is updated once per step.
- **Deep supervision**: up to `N_sup = 16` improvement steps at train time, each with a
  loss, carrying `(y, z)` forward (detached) between steps — the model learns to
  *improve* an existing answer.
- **Halting (`Q_head`)**: a small head predicts, via a binary "have I reached the
  correct solution?" signal, when to stop recursing.

## How each piece becomes an LLM operation

| TRM | headroom-recursion | Where |
|---|---|---|
| `x` | task prompt (string) | `ladder.recurse(problem=…)` |
| `y` | candidate answer (string) | threaded through `trm.run_tier` |
| `z` | reasoning scratchpad (string) | threaded through `trm.run_tier` |
| `z = net(x, y, z)` | `LATENT_UPDATE` prompt → new scratchpad | `prompts.py`, `trm.run_tier` (×`n`) |
| `y = net(y, z)` | `ANSWER_UPDATE` prompt → new answer | `prompts.py`, `trm.run_tier` |
| deep supervision, carry `(y,z)` | improvement-step loop, `(answer, scratchpad)` carried forward | `trm.run_tier` |
| `N_sup` steps | `T` steps per tier (default 3) | `config.RecurseConfig.T` |
| `n = 6` | `n` latent updates (default 6) | `config.RecurseConfig.n` |
| `Q_head` | `HALT_JUDGE` verifier → `halt_prob` | `halting.judge` |
| halt when solved | `halt_prob ≥ halt_threshold`, or convergence, or validator | `trm.run_tier` |
| "tiny beats big" | **tier ladder**, escalate only on non-halt | `ladder.recurse` |
| (new axis) | **Headroom** compresses context per call | `headroom.compress`, `claude.ClaudeClient` |

## What's deliberately different

TRM *trains* a tiny network; we do **not train** — we run the recursion at inference
against frozen Claude models. So:

- **Deep supervision** has no gradient here; its inference echo is "carry `(y, z)`
  forward and keep improving." Each improvement step is one refinement pass.
- The **tiny network** becomes the **cheapest model** (Haiku). "Less is more" then means:
  try to solve with the cheap model looping, and only pay for a bigger model when the
  cheap one plateaus. State carries *up* the ladder, so escalation resumes from the best
  draft — the analogue of the paper's detach-and-carry between steps.
- The **halt Q-head** becomes a strict self-evaluation. Because a wrong-but-stable answer
  should escalate rather than stop, **convergence alone escalates** (moves to the next
  tier); only the judge's confidence or a validator oracle **halts the whole run**.

## Defaults and why

- `n = 6` — the paper's default latent-update count.
- `T = 3` per tier — small, because each LLM step costs far more than a tiny-net step;
  we lean on tier escalation instead of many steps at one tier.
- `halt_threshold = 0.9` — require the verifier to be quite sure before stopping.
- ladder = all four Claude tiers, cheapest first.
