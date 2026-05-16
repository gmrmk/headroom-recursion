# ADR-0030: IP intel reference databases — sources, licenses, refresh discipline

- **Status:** proposed
- **Date:** 2026-05-16
- **Tags:** ip-intel, reference-data, licenses, naomi-strict

## Context

The Identity Triangulation sprint introduces a 5-source IP intel consensus
verdict (Tor / VPN / datacenter / residential / geo + ASN). Four of the
five sources are static reference databases (the fifth, ASN heuristic, is
derived in-process). The reference databases are knowledge bases that
describe IP *ranges* — they are NOT target data, so they can persist on
disk under `data/reference/` without violating Naomi-strict ephemeral
mode. But each has license terms, attribution requirements, and refresh
cadence that need to be tracked.

This ADR enumerates the sources, their license obligations, and the
refresh discipline.

## Decision

### Sources

| Source | License | Format | Size | Reader lib | Refresh |
|---|---|---|---|---|---|
| **DB-IP City Lite** | CC-BY-4.0 (attribution required) | mmdb | ~150 MB | `geoip2` (Apache-2.0) | Monthly upstream; we pull monthly |
| **DB-IP ASN Lite** | CC-BY-4.0 (attribution required) | mmdb | ~40 MB | `geoip2` (Apache-2.0) | Monthly upstream; we pull monthly |
| IP2Proxy LITE PX11 BIN | Free w/ verbatim attribution line + free download token | BIN | ~50-700 MB | `IP2Proxy` (MIT) | 24h upstream; we pull weekly |
| Tor Project bulk-exit-list | Public domain | text, IPv4-per-line | ~50 KB | stdlib `set()` | 30 min upstream; we pull every 4 h |
| X4BNet/lists_vpn | MIT | text, CIDR-per-line | ~5 MB | stdlib `ipaddress` | Automated CI rebuilds; we pull weekly |

**DB-IP Lite was chosen over MaxMind GeoLite2** because MaxMind's free
license-key issuance is gated on corporate-email approval (verified
empirically 2026-05-16). DB-IP Lite publishes drop-in-replacement
.mmdb files at `https://download.db-ip.com/free/dbip-{kind}-lite-YYYY-MM.mmdb.gz`
with no account, no key, no corporate-email gate -- and the format is
binary-identical to MaxMind's, so the same `geoip2` Python reader works
unchanged. CC-BY-4.0 attribution lands in the dossier footer.

### Storage location

All four download artifacts live under `data/reference/`. That directory
is gitignored. The bootstrap script `infra/scripts/fetch-ip-refdata.{sh,ps1}`
populates it idempotently. `make refdata` is the one-line invocation.

### Naomi-strict invariants

- `data/reference/` holds reference databases, not target data. It is
  safe to persist long-term.
- Adapter code MAY read from `data/reference/` freely; it MUST NOT write
  any target IP, target geo, or any per-investigation state into
  `data/reference/`.
- Per-investigation IP lookups live in `_CtxState.ip_lookups` (in-memory
  dict scoped to the investigation), dropped on `shred()`.
- A static-grep CI guard asserts no IP-like patterns leak into any
  persisted location outside `data/reference/`.

### Attribution

The dossier export footer must include:

```
Geolocation data: IP to City Lite by DB-IP (https://db-ip.com), CC-BY-4.0.
Proxy/VPN classification data: IP2Proxy LITE (https://lite.ip2location.com).
Tor exit-node list courtesy of the Tor Project.
Community VPN/datacenter ranges courtesy of X4BNet/lists_vpn.
```

Both the live UI footer (small print) and the static HTML export
(visible in the offline artifact) carry this block.

### Refresh discipline

`make refdata` is idempotent and supports `--force` to re-download even
fresh files. Staleness warning surfaces in the IdentityTriangulationCard
if any reference database is older than:
- Tor exit list: 24 h
- All others: 30 days

The warning is visible in both the UI and the static export.

### LGPL dynamic-linkage clause (simplekml)

The KMZ generator dependency `simplekml` is LGPL. We use it as a pure-
Python library producing a `.kmz` file artifact at investigator request.
This is dynamic linkage; the LGPL terms are satisfied by:
1. Naming the library + license in the dossier's third-party-license page.
2. Allowing the user to substitute their own version (the lib is replaceable
   via standard Python package resolution).

The dashboard's overall license remains MIT.

## Consequences

- New runtime deps in `apps/workers/pyproject.toml`: `geoip2>=4`,
  `IP2Proxy>=3`, `simplekml>=1.3`, `py-staticmaps>=0.4`,
  `spacy>=3.7`, plus the `en_core_web_sm` spaCy model.
- New first-run requirement: investigator runs `make refdata` once.
  Documented in README.
- ~150 MB of disk for `data/reference/` after first run.
- Refdata downloads need to happen over the open internet on first
  install; document this in the offline-deployment guide if/when one
  is written.
- The CC-BY-SA-4.0 attribution is the only "viral" obligation in the
  stack; the rest are MIT/Apache/public-domain/BSD-3.
