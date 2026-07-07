# Example: a small Latin-square puzzle

A task that a single Haiku call often gets subtly wrong, but the recursive loop tends to
fix — a good demonstration of the paper's thesis.

## The problem

```
Fill a 4x4 grid so every row and every column contains each of 1,2,3,4 exactly once
(a Latin square). Given clues:
  row 1: [_, 3, _, _]
  row 2: [_, _, _, 2]
  row 3: [2, _, _, _]
  row 4: [_, _, 1, _]
Also: the top-left cell is 1, and cell (row2,col1) is 3.
Return the grid as four space-separated rows.
```

## Run it

```bash
recurse "Fill a 4x4 grid ...  Return the grid as four space-separated rows."
```

With a validator oracle (recommended for structured answers), from Python:

```python
from headroom_recursion import recurse, RecurseConfig
from headroom_recursion.claude import ClaudeClient

def is_latin_square(text: str) -> bool:
    rows = [r.split() for r in text.strip().splitlines() if r.strip()]
    if len(rows) != 4 or any(len(r) != 4 for r in rows):
        return False
    want = {"1", "2", "3", "4"}
    cols = list(zip(*rows))
    return all(set(r) == want for r in rows) and all(set(c) == want for c in cols)

trace = recurse(problem, client=ClaudeClient(),
                config=RecurseConfig(validator=is_latin_square))
print(trace.summary())
```

When the validator confirms a grid, the loop halts immediately (`stop_reason:
validated`) with **no** judge call.

## What the trace looks like

`recurse --json` returns, roughly:

```jsonc
{
  "problem": "...",
  "final_answer": "1 3 4 2\n4 2 3 1\n2 4 ...",   // wraps as needed
  "halted": true,
  "stop_reason": "halt",            // or "validated" with the oracle
  "final_model": "claude-haiku-4-5-20251001",  // often solved without escalating
  "steps": [
    { "tier_model": "claude-haiku-4-5-20251001", "step_index": 0,
      "latent_calls": 6, "halt_prob": 0.4, "halted": false, "converged": false,
      "tokens_before": 5200, "tokens_after": 1900 },
    { "tier_model": "claude-haiku-4-5-20251001", "step_index": 1,
      "latent_calls": 6, "halt_prob": 0.95, "halted": true,
      "tokens_before": 6100, "tokens_after": 2100 }
  ],
  "total_calls": 16,
  "tokens_before": 11300, "tokens_after": 4000, "savings_pct": 64.6
}
```

Read it as: Haiku recursed twice; the first answer wasn't verified (`halt_prob 0.4`),
the second was (`0.95`) so it halted before escalating; Headroom cut ~65% of the tokens.

## The point

Try `recurse --no-headroom --json` and compare `savings_pct` and the answer — same
answer, far fewer tokens. Try `--steps 1` (one improvement step per tier) to see the
loop escalate up the ladder when the cheap model isn't given room to self-correct.
