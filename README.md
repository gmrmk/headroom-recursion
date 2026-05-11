# OSINT Goblin

Personal-use OSINT investigation dashboard. Built around six-primitive triangulation (name / address / phone / email / lat-long / IP) for property-vetting against vetted public databases and major lodging platforms.

This is a **single-investigator** tool. It is not designed for SaaS, not for multi-user, not for compliance audit. If those constraints change, the security and lawful-basis story has to be rewritten before shipping; see `docs/user/explanation/chain-of-custody.md` for the current honest-scope statement.

## Status (2026-05-11)

M0 exit gate green. The walking skeleton:

```
  cmd-K / Run Tool form
       └─► POST /investigations/{id}/run
              └─► Dramatiq broker (Memurai)
                     └─► tool_runner actor
                            └─► adapter (in-process or subprocess)
                                   └─► publisher → Redis pub/sub
                                          └─► API subscriber (R-6 bridge)
                                                 └─► SSE → EventStream → dossier UI
```

Currently shipping:

| Surface | State |
|---|---|
| FastAPI + SSE | live (`apps/api`) |
| Dramatiq worker + Redis broker | live (`apps/workers`) |
| Worker → API SSE bridge | live (R-6); verified by 15-min soak |
| In-process adapters | `echo`, `m0_gate_stress`, `worker_stress`, `nominatim_geocode`, `email_mx_validate`, `hibp_breach_check`, `inside_airbnb_listings` |
| Subprocess adapters | `maigret`, `true_people_search` (Scrapling), `tineye_image` (Scrapling) |
| Next.js dossier UI | live with Run Tool form + EventStream + Triage/Disprove facets |
| Evidence chain (capture → WARC → sign → TSA → hash chain) | partial; see ADR-0006 |

## Quickstart

Prereqs (`./scripts/start-dev.ps1 -Diagnose` will check all of these):
- Python 3.13+ with `uv` (or pip)
- Node 20+ with `pnpm` (or corepack)
- Memurai or WSL2 Redis on `:6379`
- Optional: Docker Desktop (only for `-Mode m1`)

First-time bootstrap:

```powershell
./scripts/start-dev.ps1 -Init
```

Subsequent runs:

```powershell
./scripts/start-dev.ps1               # m0 mode: SQLite + Memurai + local-fs MinIO
./scripts/start-dev.ps1 -Diagnose     # prereq check only; spawns nothing
./scripts/start-dev.ps1 -Mode m1      # Postgres+AGE + MinIO (needs Docker)
```

Smoke the live stack:

```bash
just smoke                            # 4-service health probe (<10s wall)
```

## Where things live

```
apps/
├── api/                          # FastAPI + SSE + Redis subscriber (L4)
└── workers/                      # Dramatiq actor + adapter registry (L4)
    └── src/osint_goblin_workers/
        ├── adapters.py           # echo, maigret, worker_stress
        └── adapters_property.py  # six-primitive property-vetting adapters

apps/web/                         # Next.js 15 dossier UI (Run Tool form, EventStream, Triage/Disprove facets)

adapters/                         # subprocess wrappers (AGPL containment + Scrapling subprocess pattern)
├── maigret/wrapper.py
├── true_people_search/wrapper.py
└── tineye/wrapper.py

packages/                         # L0–L3 internal packages
├── osint_goblin_schemas/         # L0: pubsub channels, runtime-AGPL check
├── osint_goblin_forensics/       # L1: hash chain, Ed25519 sign, RFC 3161
├── osint_goblin_db/              # L2: storage adapters
├── osint_goblin_ftm/             # L2: followthemoney entity model
├── osint_goblin_opsec/           # L2: OPSEC HUD primitives
├── osint_goblin_fetcher/         # L2: Scrapling-as-single-fetch-primitive
├── osint_goblin_adapters/        # L3: shared adapter contracts
├── osint_goblin_attestation/     # L3: chain-stamp orchestration
└── osint_goblin_evidence_pipeline/  # L3: evidence pipeline

tools/dev/                        # operator scripts
├── smoke.py                      # 4-service health probe
├── bridge_soak.py                # R-6 bridge soak (Redis half)
├── dramatiq_soak.py              # R-6b worker subprocess soak (15-min Marcus signature gate)
└── fetch-inside-airbnb.py        # download city CSV for inside_airbnb_listings adapter

docs/
├── adr/                          # architectural decision records (0001–0018, 0022)
├── architecture/                 # design notes
└── user/
    ├── explanation/chain-of-custody.md
    └── how-to/verify-in-10-min.md
```

## DAG (enforced by `.importlinter`)

```
L4: apps/api    apps/workers              (siblings — no peer imports)
L3: evidence_pipeline
L2: adapters    attestation
L1: db   ftm   opsec   fetcher
L0: schemas   forensics
```

See ADR-0022 (import-linter enforcement) and ADR-0002 (the original DAG).

## Tests

```bash
uv run pytest                              # fast loop (~13s, 148 tests)
uv run pytest -m slow                       # +M0 exit gate + bridge tests
uv run pytest -m real_network              # +external-network smoke
```

For the 15-min Marcus signature verification:

```bash
./scripts/start-dev.ps1                    # in another shell
python tools/dev/dramatiq_soak.py          # default 15min, 6 msg/min, 32 events/msg
```

Three runs is Margaret's default for discriminating deterministic-pass from flake-pass.

## What is intentionally NOT shipped

- **Multi-user authentication.** Single-investigator scope; the chain-of-custody attestation is the only identity surface (see `docs/user/explanation/chain-of-custody.md`).
- **Controller/processor doc layer.** Only relevant for SaaS distribution; gated on that future per the R-7 scope carve.
- **GHunt, BBOT, snscrape, social-analyzer, etc. in-process.** AGPL §13 forbids it; see ADR-0006. These run as out-of-namespace subprocesses or not at all.
- **Truncation defense.** Hash chain detects single-row tampering but not last-N-row deletion. Periodic external attestation is M2 work.

## License

This repository's source is the author's personal work. AGPL-licensed subprocess adapters (`adapters/<id>/wrapper.py`) carry their own license; see each wrapper's header. The combined work runs each AGPL tool in an isolated subprocess to comply with AGPL §13's distribution trigger — see ADR-0006 for the full reasoning.
