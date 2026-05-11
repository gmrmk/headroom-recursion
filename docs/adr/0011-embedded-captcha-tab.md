# ADR-0011: Embedded CAPTCHA tab inside the dashboard right-rail

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** interaction (Hideo), security (Camille), investigator (Tomás), frontend (Mei-Lan, overruled)
- **Tags:** ui, opsec, captcha, browser-profile, isolation

## Context

The fetch ladder (ADR-0002 + ADR-0009) hits manual-CAPTCHA handoff at Tier 3 (StealthyFetcher / Patchright) on hardened anti-bot targets — and unavoidably at Google SERP after Google killed the non-JS endpoint in Jan 2025 (`CONSOLIDATED-ROADMAP.md` §6). Two designs were proposed for the handoff:

- **Mei-Lan's external-tab design** (`phase3/03-frontend-engineer.md` §10): a new browser tab opened to the target page; the investigator solves the CAPTCHA in the browser; the cookies are scraped back into the per-investigation Patchright context.
- **Hideo + Camille + Tomás's embedded-tab design** (`INTEGRATION-SPEC.md` §6): a Chromium tab inside the dashboard's right rail, sharing the per-investigation `user_data_dir`, so the CAPTCHA is solved without ever leaving the dashboard.

The disagreement was resolved 3-to-1 in favor of embedded. The deciding consideration is OPSEC. The investigator persona's hour-3 frame (`phase3/06-osint-investigator.md` §0) compounds with the real-name cookie scan (SECURITY.md §6): an external tab inherits the operator's default browser profile, which means it inherits the operator's real-name cookies (Google, LinkedIn, Twitter). At hour 3 the investigator is tired; the muscle memory of "switch to Chrome to solve CAPTCHA" is exactly the kind of mistake that leaks a real-name session into a sock-account investigation. This ADR codifies the embedded design.

Mei-Lan §10 raised legitimate concerns: (a) hosting a Chromium tab inside Next.js is non-trivial; (b) the dashboard shell becomes a higher-value target for browser-class CVEs; (c) the right-rail real-estate budget is already crowded. Each is addressed in Consequences.

## Decision

CAPTCHA handoff renders **inside the dashboard's right rail** as an embedded Chromium tab, implemented as a Tauri-style WebView (the `@tauri-apps/api/webview` v2 surface, or an Electron `<webview>` tag if Tauri integration slips). The implementation choice is captured in `packages/web/src/components/captcha/CaptchaEmbed.tsx`.

Hard requirements:

- The WebView inherits the per-investigation `user_data_dir` (the same Patchright profile used by the Tier-3 fetcher). The user_data_dir path is derived as `profiles/<investigation_id>/`. No cross-investigation profile sharing.
- A real-name cookie scan runs against the WebView profile **on first mount** and **on every navigation event**; any cookie matching the operator's real-name domains (configured in `osint_goblin_opsec/realname_domains.toml`) triggers the §4 red-state HUD: full Sheet + 35% backdrop dim + action freeze.
- The WebView never opens an external tab (target=_blank handler returns false; navigation to a non-allowlisted host shows the in-dashboard "this CAPTCHA target navigated unexpectedly" sheet with a Cancel-and-Forensic-Log button).
- The CAPTCHA solve emits a `forensic_log` row (event_type=`fetch`, sub_type=`captcha_solve`) including the target URL, the time-to-solve, and the cookies-acquired hash (not the cookies themselves).
- Right-rail real-estate is reclaimed: when the WebView is mounted, the SockState tile collapses to a single-line summary. The OPSEC HUD on top remains unchanged.

## Consequences

- **Positive.** OPSEC isolation is structural. The investigator cannot accidentally solve a CAPTCHA in a real-name session; the affordance does not exist.
- **Positive.** Every CAPTCHA solve is a chain artifact. Defense counsel viewing the evidence package sees that a CAPTCHA was solved, when, and against which host. Wave-hand "I was just bypassing a bot block" is replaced by a verifiable record.
- **Positive.** The decision dovetails with ADR-0010 — the WebView's `user_data_dir` is per-investigation, the cookies in it are tied to the per-investigation sock account, and the encryption envelope of `osint_sockaccounts` covers the cookie store on shutdown.
- **Negative.** Browser-class CVEs in the WebView increase the dashboard's blast radius. Mitigation: the WebView runs in a separate process with the most restrictive Chromium sandbox flags (`--site-per-process --no-zygote --disable-gpu`); the dashboard process treats messages from the WebView as untrusted input and never re-evaluates them. `SECURITY.md` §11 "what we do NOT promise" already disclaims unspoofability of the embedded tab; this ADR reinforces.
- **Negative.** Implementation cost is real. The Tauri/Electron WebView path is non-trivial and locks the dashboard's distribution model to a desktop-app shape (vs pure web). Mitigation: at M3 if the SaaS posture changes, the WebView can be swapped for a port-forwarded local Chromium spawned by the worker — same `user_data_dir`, different host. The ADR's contract is the isolation, not the WebView implementation.
- **Negative.** The dashboard is no longer a pure browser-served webapp. M1 distribution is an installer (MSI on Win11, .pkg on macOS, .deb on Linux). Reflected in the runbook (`docs/admin/deployment-m0-spike.md` and `docs/admin/deployment-compose.md`).

## References

- `INTEGRATION-SPEC.md` §6 (embedded CAPTCHA tab decision)
- `CONSOLIDATED-ROADMAP.md` §6 risk #5 (Google SERP detection escalation)
- `phase3/02-interaction-designer.md` §10.3 (embedded design)
- `phase3/05-security-compliance.md` §11.3 FE-S5 (security accept)
- `phase3/06-osint-investigator.md` §10 (Tomás concur)
- `phase3/03-frontend-engineer.md` §10 (Mei-Lan external-tab design, overruled)
- ADR-0002 (Scrapling tiers)
- ADR-0010 (sock-account profile encryption)
- ADR-0014 (HUD freeze behavior)
