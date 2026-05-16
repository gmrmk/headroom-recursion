# ADR-0023: Adapter package location + two-surface registration discipline

- **Status:** proposed
- **Date:** 2026-05-12
- **Deciders:** Sora (Tech Lead), persona round wave-3 (parent decision) + wave-4 (sub-clause)
- **Tags:** architecture, adapters, registration, dag

## Context

Wave-3 Sprint-1 reserved this ADR for the canonical location of adapter packages in the monorepo — i.e., whether adapters live under `apps/workers/src/osint_goblin_workers/adapters/`, under a top-level `adapters/` package, or somewhere else, and what the package-naming and discovery conventions are. Sprint-1 has not kicked off as of 2026-05-12 (per Margaret roadmap §7 status check), so the parent decision body is held as a placeholder citing the wave-3 Sprint-1 commitment.

Wave-4 surfaced a separate but related structural finding that lands now regardless of the parent decision's timing. Sora's wave-4 graphify-informed deliberation (`sora-deliberation-wave4.md:22`) names a load-bearing fact the wave-3 work did not call out: **the "what adapters exist" question already has two answers in the codebase.** The worker-tier `AdapterRegistry` lives at `apps/workers/src/osint_goblin_workers/adapters.py:51` (via `_REGISTRY.register(...)`), in graph community 39. The web-tier `ADAPTERS` catalog — the list the UI uses to render runnable workflows — lives at `apps/web/src/lib/adapters-catalog.ts:55`, in graph community 49. The two communities are not connected by an import edge; the contract between them is convention, not type.

If a new adapter registers only in the worker registry, the actor can dispatch it but the UI never offers it. If it registers only in the web catalog, the UI offers a button that fails on dispatch. Today the codebase has fewer than ten adapters and manual discipline holds, but Tomás's wave-3 B1-B5 additions and the wave-4 W4-EVICT / W4-PDQ-PIPE work pull this drift surface into Sprint-4 and Sprint-5.

## Decision

We will land this ADR as a **placeholder for the parent adapter-package-location decision** (deferred until Sprint-1 kicks off and the actual file layout is exercised by real adapter code) **with one binding sub-clause that applies now**:

**Sub-clause: two-surface adapter registration.** Every adapter is registered in BOTH the worker `AdapterRegistry` at `apps/workers/src/osint_goblin_workers/adapters.py` AND the web `ADAPTERS` catalog at `apps/web/src/lib/adapters-catalog.ts`, or in NEITHER. Partial registration is forbidden. This is a discipline contract; ADR-0026 establishes the enforcement mechanism (lint) for the analogous rubric drift; the equivalent enforcement for adapter registration is reserved for a future ADR amendment once Sprint-1 reveals the actual adapter-package layout.

The parent decision (filesystem location, package boundary, manifest format, discovery mechanism) ships in a later amendment once Sprint-1 lands real adapter code. The sub-clause does not block on that — the two-surface contract holds against today's layout and any plausible Sprint-1 layout.

## Alternatives considered

- **Wait for Sprint-1 and write the full ADR at once.** *Rejected because:* the two-surface drift risk is real today and load-bearing for Sprint-4 (W4-PDQ-PIPE, W4-SUB-BRAND) and Sprint-5 (W4-EVICT) regardless of where the adapter packages eventually live. The sub-clause is independent of the parent layout question.
- **Collapse the two surfaces into one (generate the web catalog from the worker registry at build time).** *Rejected for now because:* the web catalog carries fields the worker registry does not (UI copy, severity hint, asset-graph context) and the worker registry carries fields the catalog does not (handler reference, RBAC scope). A code-generation bridge is a real future option, but it is itself a Sprint-3+ design exercise with its own ADR cost. The two-surface convention is the cheapest correct discipline today.
- **Soft-deprecate the web catalog and have the UI query the API for adapter metadata at runtime.** *Rejected because:* the catalog is render-time UX data, not runtime data; pulling it through the API adds a network hop to the UI cold-start path for no behavioural benefit, and breaks the L4 web tier's "no-server-on-cold-start" property.

## Consequences

- **Positive:** New adapters added in Sprint-4 / Sprint-5 cannot silently land in only one surface; the two-surface contract is now ADR-cited.
- **Positive:** The sub-clause is enforceable by a future lint without re-opening the parent ADR — the contract text already exists.
- **Negative:** Adding an adapter is a two-file commit forever (until/unless the code-generation alternative is adopted). Acceptable; this is the price of the two-tier metadata split.
- **Negative:** This ADR ships as a partial commitment. The parent decision is a known unfinished surface, which is unusual; the placeholder is honest about that.
- **Neutral:** The future enforcement lint has a home (ADR-0023 amendment or new ADR-0023b) but does not block Sprint-4 / Sprint-5 work — manual discipline + code review hold until the lint lands.

## Gates that re-open this

- **Gate 1 (Sprint-1 kickoff):** When Sprint-1 lands real adapter code under whatever path it chooses, write the parent-decision amendment to this ADR capturing the actual file layout, package-naming convention, and discovery mechanism. The sub-clause survives unchanged.
- **Gate 2 (drift observed):** When a code-review catches a partial-registration drift (adapter in one surface, not the other), promote the sub-clause to a lint contract immediately — do not wait for an amendment. The drift event is the trigger.
- **Gate 3 (catalog-from-registry generation):** When the worker registry and web catalog converge on enough shared metadata that the catalog could be generated, revisit the "two surfaces forever" framing and consider the code-generation bridge.

## References

- `phase6/wave4/sora-deliberation-wave4.md:22` — graphify-informed finding on the two-surface registration risk
- `phase6/MARGARET-ROADMAP-2026-05-12-wave4.html` §7 — ADR-0023-sub as Sprint-4 work; parent ADR-0023 status check
- `apps/workers/src/osint_goblin_workers/adapters.py:51` — worker `AdapterRegistry`
- `apps/web/src/lib/adapters-catalog.ts:55` — web `ADAPTERS` catalog
- ADR-0026 (proposed) — analogous lint discipline for rubric-coverage drift
- ADR-0001 — monorepo physical isolation (the broader package-layout doctrine this ADR implements at the adapter granularity)
