# ADR-0013: Tier-aware loading shapes with defensibility copy

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** interaction (Hideo), frontend (Mei-Lan), investigator (Tomás), security (Camille)
- **Tags:** ui, motion, defensibility, fetch

## Context

The four fetch tiers (ADR-0002 + ADR-0009) have empirically distinct latency profiles: Fetcher p50 900 ms, DynamicFetcher p50 5100 ms, StealthyFetcher p50 6000 ms (p95 33 000 ms), Camoufox p50 10–30 s. A single unified spinner across all four tiers fails investigators on three axes:

1. **Cost legibility.** An investigator who watches a spinner for 9 s does not know whether they spent 1 token (Fetcher edge case) or 16 tokens (Stealthy). The HUD per-engine-rate tile (ADR-0009) is the second-order surface; the per-row loading shape is the first-order surface. The hour-3 frame demands the first read tells the truth.
2. **Cancel ergonomics.** A 900 ms Fetcher reach cannot be meaningfully cancelled; a 30 s Stealthy reach can and often should be. The cancel affordance must scale with tier.
3. **Defensibility copy.** A Stealthy reach with `purpose=bypass cf_js_challenge` is a material fact in a future legal review. The chain artifact carries the copy; the UI must also surface it at the moment of the reach, so the investigator has the opportunity to abort if the copy is inappropriate for the case. This is the "informed consent at the dashboard layer" parallel to Attest's informed consent at the legal layer.

Hideo's motion-language ceiling (`phase3/02-interaction-designer.md`) caps the system at three spring presets (`pop`, `panel`, `bay`) plus `prefers-reduced-motion` hard honoring. The tier-aware shapes must fit within that ceiling — no additional springs.

## Decision

The four loading shapes are locked as follows:

| Tier | Visual shape | Cancel UI | Defensibility copy |
|---|---|---|---|
| `Fetcher` (~900 ms) | **Row pulse** (subtle opacity oscillation on the originating evidence row, `pop` preset, 800 ms cycle). No separate spinner. | `cmd-.` cancels (best-effort; race against completion). No on-row Cancel button — too fast to surface. | None displayed; the chain artifact's `tier=fetcher` field is sufficient. |
| `DynamicFetcher` (3–5 s) | **Row bar** (a 2 px progress-bar at the row's bottom edge, indeterminate animation, `panel` preset). | `cmd-.` cancels; on-row "Cancel" link appears after 1 s. | Inline subtle copy: "loading via headless browser" — informational, not gating. |
| `StealthyFetcher` (6–30 s) | **Tier-badge dialog** in the right-rail (above the SockState tile when CAPTCHA isn't mounted, below it when CAPTCHA is). Header: "Stealth fetch · `{host}`". Body: defensibility copy. Prominent Cancel button. `bay` preset for entrance. | Dialog Cancel button is the primary surface; `cmd-.` is the secondary keyboard shortcut. | **Mandatory copy displayed**, mirroring the chain artifact's `purpose` field. Default: "purpose: bypass cf_js_challenge". The copy is investigator-editable before the reach starts; the edited value is what lands in the chain. |
| `Camoufox` (10–30 s) | Same dialog shape as Stealthy; header reads "Hardened-anti-bot fetch · `{host}`". | Same Cancel. | Defensibility copy: "purpose: hardened anti-bot bypass". Same editable-before-reach contract. |

The dialog (Stealthy / Camoufox) blocks pointer events on the originating evidence card to prevent double-click double-fetch. It does **not** freeze the rest of the dashboard — the investigator can pivot to other rows while the Stealthy reach proceeds.

Empirically-measured-cost-class fallback: if a tier's wall-clock exceeds its p99 by 50%, the dialog upgrades to an amber state with a single-line "this fetch is taking longer than usual; Cancel may be wise" hint. The threshold is configurable in `config/loading_thresholds.toml`.

## Consequences

- **Positive.** Investigators read fetch cost without thinking. Row-pulse means cheap, row-bar means a few seconds, dialog means expensive *and* worth thinking about.
- **Positive.** Defensibility copy is informed-at-the-moment, not buried in chain JSON. A future cross-examination asking "did the investigator know they were running a Stealthy bypass against this host?" has a yes/no UI-side answer with a screenshot.
- **Positive.** Editable-before-reach copy gives investigators jurisdiction-specific overrides for the chain artifact ("purpose: editorial fact-check, public-interest journalism, UK PCC code") without code changes.
- **Negative.** Three distinct UI surfaces (pulse / bar / dialog) instead of one. Mitigation: the surfaces share the `LoadingShape` component with a `tier` prop; the three branches are <300 LOC total.
- **Negative.** `prefers-reduced-motion` users get static substitutes (a status-color row border for tiers 1/2, the dialog with no entrance animation for tiers 3/4). Documented in `docs/explanation/premium-feel.md` and in the accessibility test plan (`tests/a11y/`).
- **Neutral.** The dialog's right-rail placement coordinates with ADR-0011 (embedded CAPTCHA tab) and ADR-0014 (HoverCard). Layout precedence: CAPTCHA mount preempts the dialog; the dialog displays as a row-anchored popover instead, with the same content. The layout precedence is captured in `packages/web/src/components/right_rail/RailLayout.tsx`.

## References

- `INTEGRATION-SPEC.md` §6 (tier-aware loading)
- `phase3/02-interaction-designer.md` §motion-language (3-spring ceiling)
- `phase3/03-frontend-engineer.md` §9 (Framer Motion presets)
- `phase3/06-osint-investigator.md` §0 (hour-3 frame)
- `empirical/01-scrapling-smoke.md` (latency numbers)
- ADR-0002 (the tiers)
- ADR-0009 (cost-weighted governor — the second-order surface)
- ADR-0011 (CAPTCHA mount precedence)
- ADR-0014 (HUD HoverCard)
