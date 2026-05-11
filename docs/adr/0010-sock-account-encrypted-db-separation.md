# ADR-0010: Sock-account encrypted DB separation + Shamir passphrase recovery

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** security (Camille), backend (Diego), investigator (Tomás)
- **Tags:** opsec, isolation, encryption, recovery, shamir

## Context

ADR-0001 locks the structural sock-evidence isolation at the import-path layer (`db/evidence.py` vs `db/sock.py`) and the Postgres-database layer (`osint_sockaccounts` separate from `osint_evidence`). Two questions remain unsettled:

1. **Encryption at rest of the sock-account DB.** The investigator's hard rule (`phase3/06-osint-investigator.md` §8) is that a forgotten or stolen laptop must not expose the sock-account ledger. Full-disk encryption is necessary but not sufficient — a running operator with a hot disk leaks the ledger if any process can read the Postgres data files. We need a sock-specific encryption envelope above the FS layer.
2. **Passphrase recovery.** `INTEGRATION-SPEC.md` §9 left this as an unresolved STOP (#1). Three options were on the table: hardware-token (YubiKey), split-key (operator + DPO), accept-loss. Diego §B4 proposed Shamir 2-of-3; Camille §11.3 proposed 3-of-5 for org deployments. The brief (`MANUFACTURING-PLAN.md` §2.1) resolved as **2-of-3 solo / 3-of-5 org**. This ADR codifies the resolution and specifies the recovery procedure.

The sock-account passphrase guards the *operator's ability to authenticate to sock accounts*, not the evidence chain. Confusing the two is the failure mode this ADR is written to prevent. Evidence keys (per-investigation Ed25519, ADR-0008) and sock-account keys (a single operator-level secret) live in different files, different envelopes, and different recovery procedures.

## Decision

**Encryption envelope.** The `osint_sockaccounts` database is encrypted at the row level using `pgcrypto`'s `pgp_sym_encrypt`/`pgp_sym_decrypt`. The symmetric key is derived from the operator passphrase via Argon2id (memory=64 MiB, iterations=3, parallelism=4, salt stored in `accounts/sock_salt.bin`). Derivation runs once at dashboard startup; the derived key sits in the API process's heap, protected by `mlock()` on POSIX and `VirtualLock()` on Win11, and is zeroed on shutdown.

**Recovery via Shamir Secret Sharing.** The passphrase is split with `shamir-secret-sharing` (the `cryptography`-compatible threshold scheme, 256-bit shares):

| Deployment | Threshold | Shares |
|---|---|---|
| Solo / small team (default) | **2-of-3** | (a) operator-memorized share, (b) hardware-token (YubiKey 5+ FIDO2 PIN-blob), (c) printed-sealed paper share (BIP39 encoded) |
| Organization | **3-of-5** | (a) operator share, (b) hardware-token, (c) printed share, (d) DPO-held share, (e) cold-storage share (org safe) |

The RBAC role at setup exposes the choice. Both presets live in `packages/osint_goblin_opsec/sock_ledger.py` as `ShamirPreset.SOLO_2_OF_3` and `ShamirPreset.ORG_3_OF_5`. Recovery is via `scripts/sock-recover.py`, which prompts for the threshold number of shares and re-derives the passphrase; the procedure is documented in `docs/user/howto/recover-sock-passphrase.md` (H4) with the threat-model assumptions and the "what to do if one share is lost" branch.

**Accept-loss path for solo deployments.** A solo operator can opt for accept-loss at setup (`--accept-loss` flag on `sock-init`). The dashboard displays an explicit "this is unrecoverable; forgotten passphrase means the sock accounts are permanently inaccessible" warning. The accept-loss path is the *opt-in*, not the default — the default offers Shamir 2-of-3 with the warning that loss of two shares is loss of access.

## Consequences

- **Positive.** Encryption is row-level, so a hot-disk read of the Postgres data files yields ciphertext without the in-process key. The attacker must compromise the running API process to read sock-account state.
- **Positive.** Shamir gives the org the ability to revoke a single share (compromised YubiKey, departed DPO) by re-sharding; the underlying secret is unchanged but the share-set is rotated.
- **Positive.** The two presets are *named*, not configured from primitives. A future audit reading `sock_ledger.py` sees `SOLO_2_OF_3` or `ORG_3_OF_5` and reaches the correct mental model in one read.
- **Negative.** Argon2id derivation at startup is ≈ 1 s on a 2024-class laptop. Operators experience this as a perceptible cold-start cost; documented in the runbook (`docs/admin/runbook.md` §8.2) so it is not mistaken for a hang.
- **Negative.** Shamir share custody is a human-process surface. Lost-share procedures, hardware-token replacement, and printed-share reissuance are operational responsibilities that ship in the H4 how-to and the runbook §8.9.
- **Neutral.** The encryption envelope can be upgraded to per-row dedicated keys (envelope encryption with a KMS) at M3 if multi-investigator deployments demand it. The Shamir layer is unchanged in that future.

## References

- `INTEGRATION-SPEC.md` §9 (sock isolation, recovery STOP)
- `MANUFACTURING-PLAN.md` §2.1 (Shamir threshold resolution)
- `phase3/05-security-compliance.md` §11.3 (FE-S5 + INV-S2)
- `phase3/04-backend-data-engineer.md` §B4 (Shamir 2-of-3)
- `phase3/06-osint-investigator.md` §8 (sock-binding hard rule)
- ADR-0001 (the structural isolation)
- ADR-0006 (does **not** apply; AGPL containment is orthogonal to sock encryption)
