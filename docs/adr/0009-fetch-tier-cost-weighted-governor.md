# ADR-0009: Fetch-tier cost-weighted governor

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** backend (Diego), security (Camille), investigator (Tomás), interaction (Hideo)
- **Tags:** fetch, scrapling, performance, opsec, rate-limiting

## Context

ADR-0002 locks Scrapling 0.4.7 + the four-tier ladder (`Fetcher` ≈ 900 ms → `DynamicFetcher` ≈ 3–5 s → `StealthyFetcher` / Patchright ≈ 6–30 s → Camoufox opt-in 10–30 s) as the single fetch primitive. Empirical Item 1 (`empirical/01-scrapling-smoke.md`) confirmed the medians: Fetcher p50 900 ms / p95 1500 ms; Dynamic p50 5100 ms / p95 12 000 ms; Stealthy p50 6000 ms / p95 33 000 ms (one OpenSea reach was 30+ s).

A naive per-target rate-limiter (e.g. `60 req/min/host`) treats all four tiers equivalently. That is wrong on two axes:

- **Cost.** A Stealthy reach is ≈ 6–30× more expensive than a Fetcher reach in CPU, memory, browser-process lifetime, *and* anti-bot detection surface. A token-bucket that counts every reach as one token under-prices Tier 3/4 and over-prices Tier 1.
- **OPSEC.** LinkedIn ban-band is empirically <50 reaches/day/account (`CONSOLIDATED-ROADMAP.md` §6 risk #6). The investigator persona (`phase3/06-osint-investigator.md` §4) makes this a UI-visible budget, not just a backend RPS. The HUD's "LinkedIn budget" tile (`INTEGRATION-SPEC.md` §4) must reflect cost-weighted consumption, not raw-count consumption, or the tile lies at the moment that matters.

The interaction designer's tier-aware loading shapes (`INTEGRATION-SPEC.md` §6) already exist on the UI; the backend governor is the symmetric move on the dispatch side. Tomás §10 (Wayback push default = batch, not per-capture) is the same shape of cost reasoning applied to a different queue.

## Decision

The fetch governor is a **cost-weighted token bucket** keyed by `(host, sock_account_id)`. Token costs per tier are locked at:

| Tier | Cost in tokens |
|---|---|
| `Fetcher` | 1 |
| `DynamicFetcher` | 4 |
| `StealthyFetcher` (Patchright) | 16 |
| `Camoufox` | 32 |

Per-host bucket capacity defaults: 1024 tokens, refill 64 tokens/min. Per-sock-account bucket defaults: 512 tokens/day for LinkedIn-class targets (collapses to the 32-reach hard floor in `StealthyFetcher` mode), 4096 tokens/day for low-risk hosts. The HUD "per-engine rate" tile renders **bucket fill percentage**, not raw request count; the LinkedIn-budget tile renders sock-account bucket fill against the per-day cap. Exhaustion turns the tile red and freezes the action surface (consistent with INTEGRATION-SPEC §4 motion language).

Costs and bucket parameters live in `packages/osint_goblin_fetcher/governor.py` and are tuneable per deployment via `config/fetch_budget.toml`. Per-investigation overrides are signed and recorded as `forensic_log` rows (event_type=`override`).

## Consequences

- **Positive.** The HUD tiles tell the truth investigators need: a single Stealthy reach against LinkedIn empties 16 tokens, which is what actually happened to the account. Raw-count budgets would hide this.
- **Positive.** Token costs serve as a planning gate: a workflow that wants to fan out twscrape + Maigret + GHunt against the same host can be cost-priced before it runs, and the palette can sort suggestions by cost.
- **Positive.** The governor composes cleanly with the rate-tier coarse limits in `osint_goblin_opsec` — coarse limits are a ceiling; the governor is the soft predictive layer the investigator sees.
- **Negative.** Costs are an empirical calibration, not a derivation. The four numbers in the table above are anchored to Item 1 medians and the LinkedIn risk band; they need re-calibration if Scrapling 0.5.x changes tier latencies or if a target's anti-bot posture flips. Mitigation: nightly comparator-capture (`.github/workflows/nightly.yml`) flags 2× drift.
- **Negative.** The governor adds one Redis round-trip per fetch (atomic `DECRBY` + `EXPIRE`). At p50 1 ms loopback this is invisible; mentioned for completeness in `docs/reference/dramatiq-actors.md`.
- **Neutral.** The cost-table is a public surface in the runbook (`docs/admin/runbook.md` §8.2). Operators can tune it, but the defaults are the documented contract.

## References

- `INTEGRATION-SPEC.md` §4 (HUD tiles), §6 (tier-aware loading)
- `CONSOLIDATED-ROADMAP.md` §6 risk #6 (LinkedIn ban band)
- `empirical/01-scrapling-smoke.md` (tier latencies)
- `phase3/06-osint-investigator.md` §4 (sock budget visibility), §10 (Wayback push cost reasoning)
- `phase3/04-backend-data-engineer.md` §3 (fetch facade)
- ADR-0002 (the tiers themselves)
- ADR-0014 (HUD tile rendering)
