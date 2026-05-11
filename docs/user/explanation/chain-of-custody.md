# Chain of custody — what the dossier actually proves

> **Audience.** You, looking back at an investigation in six months and wondering whether the evidence still holds up. Also: anyone you hand the dossier to who asks "how do you know?"

OSINT GOBLIN doesn't just collect data. Every artifact lands in a chain that makes tampering detectable, lets you re-verify the result without trusting the dashboard, and tells you *what* you knew *when* you knew it. This document explains that chain in the order events actually happen.

## The five-stage chain

Each tool run for an investigation produces evidence through five stages. Every stage emits an SSE event you see in the dossier in real time, and every stage adds a row to the durable forensic log.

### 1. Capture (`capture-started`)

The adapter runs against the target — fetches a URL, queries a database, hits an API — and the raw response is captured **before any parsing**. This is the moment that matters legally: it's the "I observed this byte-for-byte at this UTC instant" claim. The adapter's stdout/stderr and any HTTP response body are written to a WARC file (Web ARChive — the same format the Internet Archive uses) so the capture is byte-identical to what was observed.

What this proves: you saw the source state X at time T.

What this does *not* prove: the source state X represents truth. Captures are evidence of what a website *said*, not whether it was accurate.

### 2. Write to disk (`warc-written`)

The WARC is sealed with a SHA-256 content hash and written to local storage (MinIO file:// in M0, real S3-compatible in M1). The hash is the artifact's identity from this moment on; any later alteration changes the hash and breaks the chain.

What this proves: the artifact from stage 1 is on durable storage with a stable identifier.

### 3. Sign (`ed25519-signed`)

An Ed25519 detached signature is computed over the WARC's content hash plus the investigation ID plus a UTC timestamp. The private key lives in the local keyring (never on disk, never in version control). The signature is the "I, the investigator running this instance, attest that I am the one who captured this" claim.

What this proves: this artifact was sealed by the holder of *this* private key at *this* moment.

What this does *not* prove: that the holder is who they say they are. Identity binding is a separate concern (Ed25519 key fingerprint + public-key publication).

### 4. Timestamp (`rfc3161-stamped`)

The signature is sent to an RFC 3161 Time Stamping Authority (TSA) — a third-party clock that returns a signed token binding the artifact to its UTC time. The TSA's signature anchors the timestamp to a trust root *outside* this machine, so we can prove the artifact existed by time T even if the local clock has been tampered with.

What this proves: the artifact existed before the TSA's countersignature time. Critically, this defeats backdating: you can't pre-date a TSA token because the TSA's signature is on the wire at the moment of stamping.

What this does *not* prove: the artifact represents anything true. TSA only attests to *when*.

### 5. Hash chain append (`minio-stored`)

The final stage appends a row to the per-investigation hash chain: `next_hash = sha256(prev_hash || stage_4_token)`. This makes any single-row tampering visible: if anyone alters row N, every row from N+1 onward has the wrong predecessor hash and the chain verification fails.

What this proves: the order of evidence is sealed. You can demonstrate to a third party that event N happened before event N+1 happened, and that the original evidence has not been retroactively edited.

What this does *not* prove: that no rows were dropped. A truncation attack (delete the last K rows) is not detected by the hash chain alone; it's detected by periodic external attestation (publishing the latest hash to a third-party log; this is M2 work).

## What about events that aren't captures?

Not every SSE event in the dossier is a chain-of-custody event. The dossier also surfaces:

- **`tool-run-accepted`** — the API received your "run X" request. Not yet evidence; just an acknowledgement.
- **`tool-run-result`** — adapter completed. Summary metadata (match count, timing). The underlying artifact has already been chained by stages 1–5 *if* the adapter writes a capture; some adapters (DNS-only lookups, in-memory checks) don't.
- **`tool-run-error`** — adapter failed. The error itself is logged but does not enter the chain.
- **`ftm-entity-created`** — the followthemoney parser extracted an entity from the capture. The entity references its source artifact's content hash, so you can always walk back to the capture.
- **`wayback-queued`** — a copy of the source URL was sent to the Internet Archive's Wayback Machine. This is *defense in depth*: even if your local storage is destroyed, the IA has a public timestamped copy.

## The six-primitive event vocabulary (R-5 additions)

Property-vetting adapters emit domain-specific events on top of the core chain:

- **`geocode-match`** — Nominatim resolved an address to lat/lon.
- **`person-match`** — a name/age/city lookup returned a candidate.
- **`listing-match`** — a lodging platform listing was found.
- **`breach-hit`** — an email's domain appears in a known data breach.
- **`image-match`** — a reverse-image lookup found prior occurrences.

These events carry the adapter's findings but do *not* themselves enter the hash chain — the underlying captures (WARC of the Nominatim JSON response, etc.) do, in stages 1–5 above.

## How to verify

See `verify-in-10-min.md` for the step-by-step. The short version:

1. Pick any `minio-stored` event in the dossier.
2. Read the WARC at the path the event points to.
3. Recompute its SHA-256; compare to the event's hash field.
4. Read the Ed25519 signature; verify against the published public key.
5. Read the RFC 3161 token; verify against the TSA's public certificate.
6. Walk the chain backward to row 0; confirm every `prev_hash` matches.

If steps 3, 4, 5, and 6 all pass, you have independent proof that the artifact existed at the timestamped moment, was sealed by the keyholder, and is in its original position in the chain.

## What this chain does NOT do

Honest scope boundaries:

- **It does not authenticate identity.** The Ed25519 keyholder could be anyone with access to the keyring. Identity binding is downstream (publish the public key on a long-lived identity surface — GitHub gpg keys, Keybase, etc.).
- **It does not certify truth.** A captured page that lies is still a captured page. The chain proves *you saw what you say you saw*, not that the source was honest.
- **It does not survive a full machine compromise that includes the keyring.** An attacker with your Ed25519 private key can produce convincing fake captures. Defense: rotate keys periodically; publish the public key fingerprint somewhere external; use the Wayback queue as an out-of-band second witness.
- **It does not solve truncation.** Anyone who controls the database can delete the last N rows and the chain still verifies (the new last row is internally consistent). M2 work is publishing the latest hash to a third-party append-only log so truncation is detectable.

## Why this exists

OSINT goes wrong in two ways: the investigator misremembers what they saw, and someone later challenges the timeline. The chain solves both. It is *not* legal evidence in the courtroom sense — that requires forensic chain-of-custody paperwork, witness testimony, and a much higher bar. But for the personal-use property-vetting case, it answers the practical questions: "Did I really see this?" (yes — the WARC is on disk); "When did I see it?" (the TSA token says so); "Has the dossier been tampered with since?" (the hash chain verifies).

See ADR-0006 (AGPL §13 subprocess containment) and ADR-0012 (bounded reaffirmation chain for lawful-basis attestation) for the load-bearing design decisions behind this chain.
