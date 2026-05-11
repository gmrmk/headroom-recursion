# ADR-0015: Real-name-leak full Sheet + 35% dim + action freeze

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** security (Camille), investigator (Tomás), interaction (Hideo), frontend (Mei-Lan)
- **Tags:** opsec, ui, freeze, security, override

## Context

The most damaging single failure mode in the dashboard is a real-name session leaking into a sock-account investigation. The cascade: investigator forgets a Google OAuth session in the active browser profile → opens a target page → Google sees the real-name account → the target page links the investigator's real identity to the investigation → the case is burned and the investigator is exposed.

`SECURITY.md` §6 (Tor + OPSEC leak battery) and ADR-0011 (embedded CAPTCHA tab) reduce *most* of the surfaces for this leak. They do not eliminate it. The remaining surface is the moment the investigator launches the dashboard and the startup real-name cookie scan detects an overlap *while* the dashboard is already running — e.g., the investigator manually pasted a session token into the wrong profile, or a sock-account's Patchright context picked up an unexpected real-name cookie via redirect.

The investigator persona's hard rule (`phase3/06-osint-investigator.md` §4, §8): when a real-name overlap is detected, the dashboard must **freeze every action surface** until either the leak is remediated or a signed override is recorded. Camille INV-S2 (`phase3/05-security-compliance.md` §5.2) accepts and proposes a structured-override flow rather than an unblockable freeze, on the grounds that "I know what I'm doing, this is a planned attribution scenario, and I'm signing the override" is a legitimate path that must exist.

A normal Toast or even a Dialog is insufficient surface for this state. The interaction designer (`phase3/02-interaction-designer.md` §3.4) proposes a **full-frame Sheet** with a 35% backdrop dim and `pointer-events: none` on every action surface. The Sheet is the entire dashboard's visual interruption — investigators cannot proceed without addressing it. This ADR codifies the design and the override path.

## Decision

When the OPSEC profile-tile or account-context tile transitions to red due to a real-name overlap, the following sequence fires (synchronous, all in `osint_goblin_opsec`'s state slice):

1. **Sheet mount.** A full-frame shadcn Sheet component mounts at the dashboard root, `position: fixed`, z-index above all other content. The Sheet is the size of the viewport.
2. **Backdrop dim.** The Sheet's backdrop is `bg-black/35` (35% black, hex `#0000005A`), applied behind the Sheet content and over the rest of the dashboard. Calibrated to be visually unmistakable without losing the in-dashboard context that the investigator needs to reason about the leak.
3. **Action freeze.** Every action-bearing surface in the dashboard receives `aria-disabled="true"` + `pointer-events: none` + `tabindex="-1"`. The freeze is implemented via a `data-opsec-frozen` attribute on the root layout element, and a CSS rule `[data-opsec-frozen="true"] .action { pointer-events: none; }`. The cmdk palette is included in the freeze — `Cmd-K` while frozen returns focus to the Sheet, not the palette.
4. **Sheet content.** Header: "Real-name overlap detected". Body lists the specific overlap (which cookie, which domain, which sock-account context) without revealing the cookie value itself (only the domain + a fingerprint). Two primary actions: **Remediate** (opens the remediation flow — clears the offending cookie from the relevant profile, re-scans, on green the Sheet unmounts) and **Sign override** (opens the typed-justification flow).
5. **Sign override flow.** The investigator types the literal phrase "I override real-name overlap warning" + a typed reason (≥80 chars) + their handle. The override produces a `forensic_log` row with `event_type=override`, `sub_type=realname_overlap`, and the full justification text. The Sheet unmounts; the HUD shows a `red-OVERRIDE` chip until session restart. **The override is included in every export**, with `verify.py` reporting yellow (exit-1) when overrides are present.

The Sheet cannot be dismissed by Escape, click-outside, or any keyboard shortcut other than the override flow's completion or the remediation flow's success. This is the singular surface in the dashboard that hijacks the entire screen; the discipline is to make the alarm impossible to miss without forbidding informed override.

## Consequences

- **Positive.** Real-name leaks are visually impossible to miss and structurally impossible to dismiss-by-accident. The investigator who hour-3 mis-clicks an "OK" to a Toast cannot do the equivalent here.
- **Positive.** Informed override exists as a structured chain artifact. Defense counsel reading the evidence package sees the override JSON with the typed justification, the timestamp, and the signature. There is no silent rage-bypass.
- **Positive.** The 35% dim is calibrated against the OPSEC HUD red-state ergonomics: the HUD's red tile remains visible through the dim, so the investigator's first read of the situation includes the alarm color.
- **Negative.** The Sheet hijacks the screen. An investigator in the middle of a Stealthy fetch (per ADR-0013, a 30 s reach) gets the Sheet on top of the loading dialog. Mitigation: the Stealthy reach continues in the background (the freeze is on user actions, not on async dispatches); the loading dialog is visible through the dim. The result is consistent: the investigator can't act on the result until the leak is addressed.
- **Negative.** Accessibility regressions are possible if any action-bearing surface is missed by the freeze attribute. Mitigation: a meta-test in `tests/a11y/test_opsec_freeze.spec.ts` walks the dashboard's component tree and asserts every `data-action` element honors `data-opsec-frozen`. CI gates on it.
- **Neutral.** Override-chip persistence across the session is informational; restart clears the chip. Documented in `docs/user/howto/cold-start.md` and the runbook §8.

## References

- `INTEGRATION-SPEC.md` §4 (HUD red-state motion language)
- `phase3/05-security-compliance.md` §5.2 INV-S2 (structured override)
- `phase3/06-osint-investigator.md` §4, §8, §10.G (hard pin + override accept)
- `phase3/02-interaction-designer.md` §3.4 (full Sheet design)
- `phase3/03-frontend-engineer.md` §3 (Sheet primitive)
- SECURITY.md §6 (Tor + OPSEC leak battery)
- ADR-0011 (embedded CAPTCHA tab — adjacent leak surface)
- ADR-0014 (HoverCard — red-state remediation routing)
