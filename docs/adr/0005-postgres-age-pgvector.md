# ADR-0005: Postgres + Apache AGE + pgvector at the centre

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** data architect, backend, security
- **Tags:** storage, postgres, graph, vector

## Context

Three storage tiers are mandatory:

- **OLTP + entity store** — FtM-typed rows, audit logs, investigation metadata. Relational with rich JSONB.
- **Graph queries** — entity-relationship traversals at investigation scale (10²–10⁴ entities per case).
- **Vector queries** — text embeddings (bge-small, 384-d) and image embeddings (OpenCLIP, 512-d) for similarity search and cross-modal pivots.

Three architecture options were considered:

1. **Three different stores.** Postgres + Neo4j + Qdrant / LanceDB. Mature but operationally heavy (three backup tiers, three monitoring stacks, three failure modes). Inappropriate at single-operator scale.
2. **Aleph's full stack.** Postgres + Elastic + Redis + ftm-store. Powerful at corpus scale but overkill for the investigation-class workload; explicitly rejected in `CONSOLIDATED-ROADMAP.md` §0.
3. **One Postgres at the centre.** `pgvector` + `pgvectorscale` + Apache AGE + `pg_trgm` + `tsvector` + JSONB. MinIO for blobs.

Option 3 won on operational surface area and on the data-architect's "one ACID boundary across audit + entity + vector" argument (`phase2/data-roadmap.md` §3). Empirical Item 3 (`empirical/03-apache-age-blocked.md`) **could not load AGE on Win11 Docker** in our sandbox — the smoke is staged for M1 day-1; Memgraph is the architect-pre-approved fallback.

## Decision

- **One Postgres 16 instance** with a custom image bundling AGE, pgvector, pgvectorscale, and pg_trgm. The image is the one source of truth; CI builds it.
- **Schemas:**
  - `evidence` — `forensic_log`, `artifact`, `signature`, `tsa_token`, `lawful_basis_attestation`.
  - `entity` — FtM-typed entities + `properties_ext` JSONB.
  - `graph` — AGE label graph (one graph per investigation).
  - `vector` — `embedding_text` (384-d), `embedding_image` (512-d), `face_vectors` (encrypted at rest with investigation-scoped key, separate from text/image).
- **Separate database** for `osint_sockaccounts` (see ADR-0001). Different credentials, different pool, different backup tier.
- **MinIO** for blobs (WARC, raw HTML, screenshots, artifact bodies) with compliance-mode object-lock for legal hold.
- **M1 day-1 gates:**
  - `scripts/03-age-smoke.ps1` — load AGE, create a label graph, run `MATCH` + write 100 edges, assert correctness. On fail: branch to Memgraph (Win11 MSI, broker compat documented; ADR-0005a will record the swap if it triggers).
  - `scripts/05-pgvector-bench.ps1` — write 100k 384-d vectors, p95 < 50ms HNSW query, recall@10 ≥ 0.95. On fail: tighten HNSW params; second fail: trigger ADR-0005b for the LanceDB swap.

## Consequences

- **Positive.** One backup tier, one monitoring stack, one connection pool. Single ACID boundary across audit + entity + vector.
- **Positive.** AGE-style Cypher feels native to graph-thinking investigators; pgvector HNSW is fast enough for 100k-scale.
- **Negative.** AGE on Win11 Docker is **unverified**. M1 day-1 may force the Memgraph fallback. We accept this risk because the cost of the swap is bounded (replace AGE driver, keep entity + audit + vector unchanged).
- **Negative.** pgvector at ≥1M vectors is a watch. The `EmbeddingStore` protocol + LanceDB skeleton lands in M1 even if pgvector is fine, so the M2 swap is a config flip, not a port.
- **Neutral.** LiteFS audit-split is deferred to M2 with explicit criteria (recall@10 threshold, p95 threshold, write count); no premature optimization.

## References

- `INTEGRATION-SPEC.md` §1
- `CONSOLIDATED-ROADMAP.md` §1 (stack), §3 (Item 3, Item 5), §5 (unresolved-2)
- `phase2/data-roadmap.md` §3, §D-A3
- `empirical/03-apache-age-blocked.md`
- `empirical/05-embedding-pipeline-partial.md`
- ADR-0004 (FtM entities live here)
- ADR-0008 (forensic_log lives here)
