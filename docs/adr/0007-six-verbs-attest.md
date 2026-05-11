# ADR-0007: Six verbs including Attest; Compare deferred to M2

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** interaction, investigator, security, architect
- **Tags:** ux, vocabulary, compliance

## Context

The dashboard is keyboard-first via a cmdk-style palette. The action vocabulary must be small (or it stops being memorable at hour 3) and stable (or investigators relearn the spine every release). Phase 2 proposed five verbs: **Pivot, Capture, Annotate, Cite, Export**. Phase 3 personas surfaced two pressure-points:

- The interaction designer (`phase3/02-interaction-designer.md` §1) argued **Attest** should be a verb, not a sub-modal. Rationale: the EU AI Act 2026 + GDPR Art.9 lawful-basis attestation is a recurring action investigators perform across many investigations; burying it in a per-action modal trains the investigator to dismiss it. Promoting it to a verb makes its keystroke (`A` in the palette) part of the muscle memory, and the chain artifact it produces is a first-class object.
- The investigator (`phase3/06-osint-investigator.md` §10.G) demanded **Compare** — side-by-side dossier cards with hash-distance overlay and a "merge candidate" button. Maltego and Hunchly have an analogous operation; we don't.

The interaction designer's promotion proposal aligns with the security persona's STRIDE analysis (`phase3/05-security-compliance.md` §1.2 surface S7), which says biometric processing repudiation is mitigated by every face-match event logging a prompt-attestation artifact. A verb produces an artifact more naturally than a modal does.

The investigator's Compare demand is real, but the IA / interaction surface around it (merge-candidate review, FtM `compare()` ER tie-break, LLM-as-judge for the 0.4–0.7 probabilistic band) is the long pole. Compare cannot land at M1 without compromising the other six.

## Decision

The M1 vocabulary is locked at **six verbs**:

| Verb | What it does | Cost target |
|---|---|---|
| **Pivot** | Spawn a new claim chain from any evidence card | ≤2 keystrokes (`p` then Enter) |
| **Capture** | Persist a fetched artifact to forensic_log + MinIO with Ed25519 + RFC3161 stamps | 1 keystroke (`c`) |
| **Annotate** | Attach an investigator note to an entity, claim, or artifact | 1 keystroke (`a`) |
| **Cite** | Emit a citation entry (CSL-JSON + FtM-JSONL) referencing a specific artifact | 1 keystroke (from cite-mode) |
| **Export** | Generate an evidence-package zip (WARC + sigs + manifest + FtM JSON) | `cmd-E` |
| **Attest** | Sign a lawful-basis attestation; required gate before biometric matches | typed phrase + signature |

**Compare** is reserved as the 7th verb, scheduled for M2 with a single new endpoint `POST /investigations/{id}/compare`. The vocabulary is **stable from M1 forward**; investigators do not re-learn the spine across releases.

Palette ranking, scope nesting, and OPSEC-blocked-action ordering are specified in `docs/reference/keyboard.md`. Reaffirmation chain (≤20 reaffirmations or ≤24h, whichever first) is documented in `docs/explanation/lawful-basis.md`.

## Consequences

- **Positive.** Compliance is not a hidden modal; it is a verb. The chain artifact is a first-class object that ships in every export. Defense-counsel admissibility argument strengthens (every face match has an artifact, not just a log line).
- **Positive.** Six verbs fit on one cmdk root row. Empty-state shows the W1–W8 workflow grid, not the verbs (investigators don't have things to act on yet).
- **Negative.** The investigator's Compare demand is real and unmet until M2. Mitigated by surfacing PDQ-hash-distance + "matched on 3 platforms" affordance in the Pivot suggestions (rule R10 in `phase2/investigator-roadmap.md` §10).
- **Neutral.** When Compare lands in M2, it is the 7th verb — not a renumbering or a vocabulary churn. The keystroke is `m` (for "merge") to avoid `c` collision with Capture / Cite.

## References

- `INTEGRATION-SPEC.md` §2 (locked vocabulary)
- `CONSOLIDATED-ROADMAP.md` §1 (frontend stack)
- `phase3/02-interaction-designer.md` §1 (Attest verb promotion)
- `phase3/06-osint-investigator.md` §10.G (Compare demand)
- `phase3/05-security-compliance.md` §1.2 S7, §5.1 (Attest as chain artifact)
- ADR-0008 (Export produces the M1 evidence-package zip)
