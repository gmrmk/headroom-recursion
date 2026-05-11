# ADR-0017: cmd-K command palette specification

**Status:** Accepted (Sprint 1, WI-0114)
**Date:** 2026-05-11
**Authors:** Hideo Vance (Interaction), Iris Lindenmayer (IA)
**Cites:** INTEGRATION-SPEC §5; phase3/02-interaction-designer.md §3; phase3/01-information-architect.md §10.2

## Context

The cmd-K command palette is the **spine** of OSINT GOBLIN — every primary verb is reachable in ≤2 keystrokes from any focus. Without a frozen spec before Sprint-2 begins, the empty-state, ranking, scope-narrowing, and OPSEC-blocked-action UX will be re-litigated through implementation noise. Hideo §3 locked the contract; this ADR makes it canonical.

## Decision

### 1. Bindings (two distinct, NOT auto-detected scope)

| Binding | Scope | Behavior |
|---|---|---|
| `cmd-K` (macOS) / `ctrl-K` (Win/Linux) | **Root** | Open palette in root scope (workflows, verbs, entities, tools, settings, cases, help) |
| `cmd-Shift-K` / `ctrl-Shift-K` | **Scoped** | Open palette pre-filtered to current investigation context |
| `Escape` | Modal | Dismiss top-most modal/palette |

Implementation: `apps/web/src/lib/keyboard.ts` `KEY_BINDINGS` table is the **single registry**. No component attaches `window.addEventListener('keydown', ...)` directly (Iris §10.2).

### 2. Primitive

Built on `cmdk` (paco-cmdk-based shadcn primitive). **`shouldFilter={false}`** + custom ranking via the `filter` callback (we do not use cmdk's built-in fuzzy because we need the 6-input score).

### 3. Empty state

- **Default** (0–4 palette actions in this investigation): **W1–W8 workflow grid** on top, recents below.
- **Warm** (≥5 palette actions): **recents on top**, W1–W8 grid demoted to bottom (always visible, never auto-hidden).

The workflow grid uses the locked W1–W8 two-letter prefixes:

| Prefix | Workflow |
|---|---|
| `un` | Username Dossier (W1) |
| `em` | Email → breach → accounts (W2) |
| `ph` | Phone → carrier → pivot (W3) |
| `im` | Image → reverse + EXIF + geo (W4) |
| `do` | Domain → CT + Wayback + subfinder (W5) |
| `pe` | Person → financial + corporate + sanctions (W6) |
| `fa` | Face match (biometric gate) (W7) |
| `ge` | Event geolocation (W8) |

### 4. Ranking — 6-input weighted score

For each candidate item `c` against query `q`:

```
score(c, q) =  1.0 * exact_prefix(c, q)
             + 0.6 * fuzzy(c, q)
             + 0.3 * recency(c)
             + 0.2 * frequency(c)
             + 0.4 * context_fit(c, current_investigation)
             - opsec_penalty(c)
```

Where:
- `exact_prefix` = 1 if `q` is a case-insensitive prefix of the candidate's canonical name; 0 otherwise.
- `fuzzy` = subsequence-match fraction (0..1) using a deterministic Smith-Waterman-like scorer.
- `recency` = `exp(-Δt / 24h)` where `Δt` = time since last invocation by this investigator.
- `frequency` = `min(uses/20, 1.0)` over the trailing 30 days.
- `context_fit` = 1 if the verb/tool is relevant to the current investigation's primary subject type (username / email / phone / domain / person / face / event); 0 otherwise.
- `opsec_penalty` = 0 if all OPSEC tiles green; 0.5 if any amber; 2.0 if any red (red-OPSEC items sort last but remain visible).

### 5. Scope chips, NOT Lucene

Investigators **type plain text**. They never need to learn `dataset:` / `entity_type:` / `country:` syntax. Scope narrowing is via:
- The `cmd-Shift-K` binding (investigation-scoped from the start).
- Type-then-Tab-to-narrow: after `un` matches W1, Tab narrows to the username sub-picker.

Lucene-on-top is the Aleph anti-pattern this ADR explicitly rejects.

### 6. OPSEC-blocked actions

- Render with a **lock glyph** in the row.
- **Sort last** but remain visible (do not hide — the investigator needs to see what's locked).
- `Enter` opens the **remediation surface** (which OPSEC tile is red and how to clear), NOT the action.

### 7. Result rendering

- Linear-style 80ms debounced preview pane on the right of the palette.
- Each row: icon · canonical name · keyboard hint (e.g. `un` for W1) · ranked score (debug-mode only) · OPSEC chip (if blocked).

### 8. Out of scope (explicit non-goals)

- **No 7th verb in M1.** The 6 verbs (Pivot, Capture, Annotate, Cite, Export, Attest) are the closed set per ADR-0007. Compare lands as a 7th verb in M2 only after explicit ADR amendment.
- **No query operators** (no `type:` / `tier:` / `from:`). If filtering is needed, it lives on the focused surface, not the palette.
- **No multi-select.** One item selected per palette invocation. Multi-target Pivot is `Shift-P` from the dossier, not from cmd-K.

## Consequences

**Positive:**
- Ranking is deterministic, testable, and tunable independently (fixture at `tests/golden_path_e2e/cmdk_ranking_fixture.json` — ≥30 cases mandatory).
- Single keyboard binding registry prevents collisions discovered only at Sprint-6 implementation.
- Empty-state policy is explicit; no "what should the palette show before the user types?" debate in Sprint 2.

**Negative / accepted:**
- Two bindings (`cmd-K` + `cmd-Shift-K`) rather than one auto-detected — but Iris §10.1 argued and won that auto-detect-scope is too magical.
- Ranking requires per-investigation state (recents, frequency) — adds Zustand slice complexity. Accepted; the slice already exists for OPSEC HUD state.

## References

- `apps/web/src/lib/keyboard.ts` — central binding registry
- `apps/web/src/components/command-palette.tsx` — palette component (Sprint-1 stub; full impl WI-0606 Sprint 1)
- `tests/golden_path_e2e/cmdk_ranking_fixture.json` — 30+ ranking test cases (this WI)
- `tests/golden_path_e2e/test_cmdk_ranking.py` — fixture-driven test (this WI; xfail until WI-0606 lands the real ranker)
- INTEGRATION-SPEC §5 — cmd-K spine architecture
- phase3/02-interaction-designer.md §3 — Hideo's original 6-input formula
- phase3/01-information-architect.md §10.2 — Iris's central-binding-table mandate
