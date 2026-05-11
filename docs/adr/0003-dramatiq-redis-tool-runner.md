# ADR-0003: Dramatiq + Redis with a single `tool_runner` actor

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** backend, architect
- **Tags:** stack, orchestration, queue, win11

## Context

The dashboard fans out 12 M1 tool adapters per investigation kickoff, each producing tens to hundreds of artifacts that pass through the evidence pipeline (Merkle → Ed25519 → RFC3161 → MinIO → FtM → SSE). Three task-queue options were evaluated in Phase 1b:

- **Celery** — dropped Win11 native support in 2016 (`prefork` pool only runs on POSIX). Disqualified.
- **arq** — Redis-based, async-first, but the project is maintenance-only (last substantive release in 2023, single maintainer). Disqualified on supply-chain risk.
- **RQ** — runs on Win11, simple, but benchmarked ~10× slower than Dramatiq on equivalent workloads and lacks first-class middleware for retries / dead-letter / rate-limits.

**Dramatiq + Redis** survived the cut. Empirical Item 4 (`empirical/04-dramatiq-redis-win11.md`) verified Win11 native via `StubBroker`: 5/5 messages round-trip, sub-millisecond. Redis path is one-liner via Memurai (Win11 native MSI) or WSL2.

The Phase 3 backend spec (`phase3/04-backend-data-engineer.md` §B7) further argued that **one actor for 12 adapters** is structurally better than 12 separate actors: one place to enforce idempotency keys, one place to apply rate limits, one place to enforce the AGPL §13 subprocess/in-process branch.

## Decision

- **Broker:** Dramatiq + Redis. Memurai for Win11-native dev; sibling container in Docker Compose for prod.
- **Single actor:**

  ```python
  @dramatiq.actor(queue_name="osint_tools", max_retries=2, time_limit=300_000)
  def tool_runner(req: dict) -> None:
      adapter = registry.get(req["adapter_id"])   # subprocess for AGPL, in-process for FOSS
      result  = adapter.run(req["payload"], investigation_id=req["investigation_id"])
      evidence_pipeline.send(result)              # Merkle → Ed25519 → RFC3161 → MinIO → FtM → SSE
  ```

- **Adapter registry** is the new-tool plug point. New tools land as registry entries, not new actors. The registry distinguishes `LicenceClass.AGPL_SUBPROCESS` from `LicenceClass.FOSS_INPROCESS`; mis-classification is caught by ADR-0006's CI lint.
- **Idempotency.** Each request carries a deterministic key `(investigation_id, adapter_id, payload_hash)`; duplicate keys within a 24-hour window are deduplicated.
- **Retries.** `max_retries=2` for transient (network, 5xx); zero for adapter exit-code errors. Dead-letter to `osint_tools_dead`.
- **Rate limits.** Per-engine governor (`investigator-roadmap.md` §4) lives in the actor middleware, not in the adapter.

## Consequences

- **Positive.** Win11 native dev is one Memurai install away. CI uses `StubBroker`; no Redis dependency in test.
- **Positive.** One observable queue. One Grafana dashboard. One place to add a feature flag.
- **Positive.** Adapter authors implement a 50-LOC class, not a Dramatiq actor.
- **Negative.** Single-actor head-of-line blocking on a slow adapter is a real risk. Mitigated by per-adapter worker-process partitioning (`queue_name="osint_tools_long"` for `StealthyFetcher`-heavy adapters) at M1+.
- **Neutral.** Memurai is a Redis-API-compatible drop-in. If we switch to native Redis on WSL2 later, no code changes.

## References

- `INTEGRATION-SPEC.md` §10 (backend topology)
- `CONSOLIDATED-ROADMAP.md` §1, §3 (Item 4)
- `empirical/04-dramatiq-redis-win11.md`
- `phase3/04-backend-data-engineer.md` §B7
- ADR-0002 (the fetch facade the adapters call)
- ADR-0006 (AGPL subprocess/in-process branch lives in the registry)
