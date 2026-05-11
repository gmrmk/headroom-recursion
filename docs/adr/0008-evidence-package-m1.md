# ADR-0008: Evidence package ships in M1, not M2

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** investigator, security, architect, backend
- **Tags:** evidence, forensics, milestone, defensibility

## Context

The original Phase 2 architect roadmap scheduled the full evidence-package zip — WARC + Ed25519 + Merkle + RFC3161 + standalone `verify.py` — for M2. The investigator persona (`phase3/06-osint-investigator.md` §10.H) flagged this as the **"would I switch?" trigger**: if the M1 dashboard can only export a PDF and the WARC-zip bundle ships at M2, investigators keep capturing in Hunchly in parallel as belt-and-braces, and the dashboard fails its modal use ("the artifact a court will accept").

The security persona (`phase3/05-security-compliance.md` §5, INV-S3) escalated: all four load-bearing components are buildable in two sprints because their primitives are independent and each sub-team verdict locked them:

- **Ed25519** signing — `cryptography` 48.0.0, sub-millisecond per signature, primitive selection locked in `phase3/subagent-security-ed25519.md`.
- **hash chain** — Postgres advisory-lock per `case_id`, empirically 186K rows/sec across 8 threads (`phase3/subagent-security-merkle.md`), tamper detection at row N+1.
- **RFC 3161** — three-TSA fan-out (FreeTSA primary, DFN + Apple fallback), verification semantics + FRE 901(b)(9) admissibility argument in `phase3/subagent-security-rfc3161.md`.
- **WARC layout** — Webrecorder / `warcio`, layout specified in `phase3/05-security-compliance.md` §5.

The remaining work is integration + `verify.py` polish, not six sprints. **Ship M1.**

A Phase 3 backend nuance: the M1-vs-M2 split lands as **"M1 ships zip with placeholder TSA, M2 hardens real RFC3161"** in `phase3/04-backend-data-engineer.md` §B3. The investigator persona accepted the deal in §8. This ADR records the full bundle as M1; the TSA placeholder for the M1-window is captured in this ADR's Consequences and in `docs/reference/evidence-package.md`.

## Decision

The M1 export verb produces a standalone-verifiable zip with this layout:

```
case_<id>.zip
├── manifest.json                # case metadata, signing pubkey, TSA cert digests, genesis hash
├── manifest.sig                 # 64-byte Ed25519 over manifest.json (HKDF "manifest-v1")
├── forensic_log.jsonl           # one row per line; signature + tsa_tokens inline
├── artifacts/
│   ├── <artifact_id>.warc.zst   # captured bytes; zstd level 19 for archival ratio
│   ├── <artifact_id>.sig        # 64-byte Ed25519
│   └── <artifact_id>.tsr/
│       ├── freetsa.tsr.bin
│       ├── dfn.tsr.bin
│       └── apple.tsr.bin
├── ftm.jsonl                    # FtM-typed entities (operational layer)
├── attestations/
│   ├── lawful_basis_<n>.json
│   ├── lawful_basis_<n>.sig
│   ├── reaffirm_<m>.json
│   └── reaffirm_<m>.sig
├── overrides/                   # only if any structured-override events fired
├── tsa_cas.pem                  # vendored TSA CA certs for offline verify.py
├── verify.py                    # standalone verifier — no network, no dashboard
└── README.md                    # human-readable verification narrative
```

`verify.py` exit codes:

- `0` — chain intact and all TSRs verify.
- `1` — chain intact, partial TSR coverage (yellow). Defense counsel reads this as "two of three timestamp witnesses agree."
- `2` — chain mismatch (red, P0).

**M1 TSA posture.** The three-TSA fan-out (FreeTSA + DFN + Apple) is the M1 default. If a TSA outage degrades coverage during the M1 window, `verify.py` reports yellow and the chain is still produced. There is **no placeholder local-clock TSA path in production exports**; the backend B3 deal applies only to internal pre-M1 spike artifacts, not to investigator-issued zips.

## Consequences

- **Positive.** The dashboard ships an artifact a defense lawyer can verify two years later on any Python install. The investigator's "would I switch?" answer flips from no to yes at M1.
- **Positive.** FRE 901(b)(9) admissibility argument has standing immediately, not deferred.
- **Positive.** Standalone `verify.py` is also a marketing surface — distributing it (with a sample zip) to defense counsel as a one-pager is a credibility multiplier.
- **Negative.** M1 scope is larger. Two sprints of integration + `verify.py` polish are real work, not an estimate-cushion. Mitigated by sub-team verdicts having already locked each primitive.
- **Negative.** Long-term archival (ETSI EN 319 122 CAdES-A re-timestamping before TSA cert expiry) is deferred to M3+. FreeTSA's Feb-2040 horizon makes this acceptable for the 2026 ship.
- **Neutral.** The WARC writer's cookie sanitization (strip `Set-Cookie` / `Cookie` headers from request/response bytes before signing) is captured here for traceability; the actual code lives in `evidence/warc_writer.py`.

## References

- `INTEGRATION-SPEC.md` §7 (M1 evidence package)
- `CONSOLIDATED-ROADMAP.md` §2 (M1 upgraded scope)
- `phase3/05-security-compliance.md` §5
- `phase3/subagent-security-ed25519.md`, `subagent-security-merkle.md`, `subagent-security-rfc3161.md`
- `phase3/04-backend-data-engineer.md` §B3
- `phase3/06-osint-investigator.md` §10.H
- ADR-0001 (verify.py lives in `evidence/`)
- ADR-0007 (the Export verb is the trigger)
