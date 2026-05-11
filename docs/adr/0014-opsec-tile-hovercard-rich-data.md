# ADR-0014: OPSEC tile HoverCard with rich-data drill-down

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** interaction (Hideo), frontend (Mei-Lan), investigator (Tomás), information-architect (Iris)
- **Tags:** ui, opsec, hud, hovercard, accessibility

## Context

The OPSEC HUD (`INTEGRATION-SPEC.md` §4) is non-dismissable and shows six tiles: Tor circuit · Browser profile · Account context · LinkedIn budget · Per-engine rate · Biometric lawful-basis TTL. Each tile has a green / amber / red color state. The minimum-viable rendering is a colored dot with a label.

That rendering fails investigators on the questions they actually ask. Examples drawn from the Phase 3 walkthroughs:

- *Tor circuit amber* — but **which** circuit replaced when, exit country was where, did DNS leak get re-tested? The investigator's first reach after the amber must include this information.
- *LinkedIn budget 30–49* — but **of what total**, **across which sock accounts**, with **how many tokens consumed by which fetches** (per ADR-0009 cost-weighting)?
- *Per-engine rate 60–90%* — but **which engine**, **which host bucket**, **time-to-refill**?

A modal-on-click would interrupt the hour-3 flow. A persistent expanded HUD would consume screen real-estate (`INTEGRATION-SPEC.md` §3 already compresses to a tray on <1440 wide). The interaction designer's solution: **HoverCard on each tile**, non-modal, dismisses-on-mouseout, click-pins-open for keyboard users.

Mei-Lan §3 raised the accessibility question: HoverCard on hover does not satisfy keyboard navigation; the same surface must be reachable via Tab + Enter, and once opened via keyboard it must persist until Escape. This ADR codifies both interactions.

## Decision

Each of the six HUD tiles renders as a `Tile + HoverCard` pair using the shadcn `HoverCard` primitive (Radix UI underlying). Behavior:

- **Hover open:** 150 ms delay, dismiss on mouseout with 150 ms grace.
- **Keyboard open:** Tab to the tile, Enter or Space toggles the HoverCard into pinned-open state; Escape closes; arrow keys navigate adjacent tiles' HoverCards.
- **Pinned-open state** persists across mouse movement until Escape, click outside, or a state change in the tile (e.g. amber → red transitions also auto-close the prior HoverCard).
- **Content per tile** (the canonical rendering of `phase3/02-interaction-designer.md` §HUD-rich-data spec):

| Tile | HoverCard contents |
|---|---|
| **Tor circuit** | exit country (flag + ISO), circuit fingerprint (truncated), DNS-leak last-test timestamp + result, WebRTC-leak status, "rotate now" button + "test leaks now" button |
| **Browser profile** | active profile path, real-name cookie scan last-run + result, profile creation date, "switch profile" link (opens sock-ledger view in left rail) |
| **Account context** | active sock-account handle, target platforms bound, last-action timestamp, "unbind" + "spawn new sock" actions |
| **LinkedIn budget** | bucket fill % with raw `tokens / cap`, time-to-next-refill, last-5-fetches table with tier + tokens-consumed, "view all" link (opens full sock budget reference page) |
| **Per-engine rate** | top-3 host buckets by fill, time-to-refill per bucket, cost-table footnote, "view all engines" link |
| **Biometric lawful-basis** | TTL countdown to bound, reaffirmation count of bound (e.g. "5 of 20"), case-wide bound time-to-expiry, "view attestation chain" link (opens evidence-package preview filtered to attestations) |

Red-state tiles still freeze the action surface per INTEGRATION-SPEC §4; the HoverCard above a red tile shows the remediation steps and the structured-override entry-point (typed-justification + signature). The override surface is the only path to clearing a red state; the HoverCard is not the override surface, just the routing point to it.

Layout: HoverCard pops downward from the tile, 4 px gap, `panel` motion preset for entry. Max width 360 px, content scrolls if it overflows. The HoverCard never overlaps the embedded CAPTCHA tab — when CAPTCHA is mounted, HoverCard positioning shifts to leftward popout for the right-most tiles.

## Consequences

- **Positive.** Every "but actually which one?" question the investigator asks is one hover away from an answer. The HUD goes from "alarm color" to "alarm color + actionable diagnosis."
- **Positive.** The HoverCard is keyboard-accessible by design (Radix primitive); investigators on screen readers get the same affordance.
- **Positive.** The HUD's "tells the truth" promise (ADR-0009 + ADR-0010) is rendered, not just stored. A LinkedIn budget tile at 35% with a HoverCard showing the last-5-fetches table is auditable; a colored dot is not.
- **Negative.** Six HoverCards × six content templates = real frontend work. Mitigation: the HoverCards share a `HudHoverContent<T>` component with per-tile content slots; the work is ~6 small components, not six monoliths. Tracked in WI-0701 in the Sprint 7 OPSEC-HUD work.
- **Negative.** Hover-on-touch is a UX dead-end. The dashboard's primary target is laptop investigators with keyboard + trackpad; touch is out of scope at M1. Documented in `SECURITY.md` §11 (what we do NOT promise) and in the accessibility test plan.
- **Neutral.** The HoverCard contract for "view chain" links coordinates with the evidence-package preview pane (M2). The M1 HoverCard's "view all" links are routes that exist as placeholders; the M2 work fills them.

## References

- `INTEGRATION-SPEC.md` §4 (OPSEC HUD tiles)
- `phase3/02-interaction-designer.md` §HUD-rich-data
- `phase3/03-frontend-engineer.md` §3 (component tree)
- `phase3/01-information-architect.md` §IA-shell
- `phase3/06-osint-investigator.md` §0 (hour-3 frame), §4 (sock budget visibility)
- ADR-0009 (cost-weighted budget — the source of "tokens" data)
- ADR-0010 (sock-account encryption — drives profile listing)
- ADR-0013 (loading shapes — coordinates layout precedence)
- ADR-0015 (real-name leak Sheet — the red-state surface above HoverCard)
