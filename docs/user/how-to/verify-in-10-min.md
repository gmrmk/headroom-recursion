# Verify a dossier in 10 minutes

> **Goal.** Independently confirm a dossier's evidence chain without trusting the OSINT GOBLIN dashboard. You should be able to do this with `openssl`, `sha256sum`, and a text editor — nothing OSINT-GOBLIN-specific is required beyond reading the chain rows.

This walk-through assumes you have:

- Access to the investigation's storage directory (the MinIO/file:// path).
- The investigation ID (UUID).
- The Ed25519 public key fingerprint (from a separately-published source — not from this dashboard).
- The TSA's public certificate (every TSA publishes one; pinned by URL in the dossier metadata).

If you don't have those four, you don't have an independently verifiable dossier — you have a screenshot. That's a separate problem; this guide can't fix it.

For background on what each chain stage means, see [chain-of-custody.md](../explanation/chain-of-custody.md).

## The 10-minute battery

### Step 1: Find a `minio-stored` event in the dossier (1 min)

In the dossier UI, click any row with the green `minio-stored` event_type. The payload will contain:

```json
{
  "warc_path": "data/minio-fs/{investigation_id}/{run_id}.warc.gz",
  "content_hash": "sha256:abc123...",
  "signature_path": ".../sig.bin",
  "tsa_token_path": ".../tsa.tsr"
}
```

If you don't see these fields, the artifact didn't make it to stage 5 of the chain (e.g. M0 in-process emitters skip the chain entirely — that's by design; they're testing the SSE pipe, not the evidence pipe).

### Step 2: Recompute the content hash (1 min)

```bash
sha256sum "$WARC_PATH"
```

Compare the output to the `content_hash` field. They must match byte-for-byte. If they don't, either:

- The WARC was modified after storage (chain is broken), OR
- You're looking at a different artifact than the dossier references (path mismatch).

Both are red flags. Stop verification and investigate.

### Step 3: Verify the Ed25519 signature (2 min)

```bash
openssl pkeyutl -verify \
  -inkey investigator_pubkey.pem -pubin \
  -in "$SIGNATURE_PATH" \
  -sigfile "$SIGNATURE_PATH" \
  -rawin -in "$WARC_PATH"
```

(Exact incantation varies by openssl version; the M1 docs ship the verified one-liner.)

If verification passes, the WARC was signed by the holder of the private key matching `investigator_pubkey.pem`. If it fails, the WARC has been altered since signing, or you have the wrong public key.

**Identity binding check:** the public key must come from a source *outside* this dashboard. GitHub gpg-keys page, Keybase, a published identity document — anywhere the dashboard couldn't have manipulated. If the only source for the public key is the same machine that ran the investigation, you have a closed-loop attestation and the chain doesn't actually prove anything to an outsider.

### Step 4: Verify the RFC 3161 token (3 min)

```bash
openssl ts -verify \
  -in "$TSA_TOKEN_PATH" \
  -data "$WARC_PATH" \
  -CAfile tsa_root.pem
```

`tsa_root.pem` is the TSA's published root certificate (Sectigo, DigiCert, FreeTSA, etc.). The token includes a UTC timestamp — read it and confirm it's consistent with the dossier's recorded run time:

```bash
openssl ts -reply -in "$TSA_TOKEN_PATH" -text | grep -i "Time stamp:"
```

The TSA timestamp is the *anchor* — it proves the artifact existed by that wall-clock moment. The local clock cannot fake this.

### Step 5: Walk the hash chain backward (3 min)

The hash chain is stored alongside the WARCs:

```
data/minio-fs/{investigation_id}/_chain.jsonl
```

Each line is one row:

```json
{"row": 12, "ts": "2026-05-11T15:42:01Z", "artifact": "abc123...", "prev_hash": "def456...", "this_hash": "ghi789..."}
```

For row N, verify: `sha256(prev_hash || artifact || ts) == this_hash`.

Walk backward from your row to row 0 (the genesis row, which has `prev_hash` = 64 zeros). Any mismatch breaks the chain. The genesis row's `this_hash` is the chain root; if you saved it elsewhere when the investigation started, you can also confirm the chain hasn't been *replaced* (a fresh-start tampering attack).

### Step 6: Cross-check the Wayback queue (1 min, optional but recommended)

If the dossier shows a `wayback-queued` event for the same artifact, look up the source URL on `web.archive.org`. A copy timestamped near your run's wall-clock confirms an independent third party (the Internet Archive) saw the same source at the same time.

This is *out-of-band* corroboration — the strongest evidence you can get for "the source said X at time T" without subpoenaing the source itself.

## What "verified" means after these 6 steps

If steps 2, 3, 4, and 5 all pass, you have proven, without trusting the dashboard:

- **What:** the WARC byte-for-byte represents what the adapter captured.
- **Who:** the WARC was signed by the holder of the private key matching the public key you verified externally.
- **When:** the TSA token's timestamp predates any post-hoc fabrication (the artifact existed by that wall-clock moment).
- **Order:** the artifact's row position in the chain is fixed — no later event has been retroactively inserted before it without breaking every subsequent row's hash.

Step 6 adds a third-party corroboration of the *source* state, which is the only step that proves anything about the world outside the investigator's machine.

## What you have NOT proven, even with all 6 steps green

- **Source honesty.** The captured page may have been lying. A `breach-hit` on `example.com` means the domain was breached, not that the email actually appears in the breach.
- **Investigator honesty.** A holder of the private key can sign anything they want, including a fabricated WARC. The chain proves consistency, not truthfulness of the original observation.
- **No truncation.** If the chain ends at row 50 and you're verifying row 47, you can't tell whether rows 51–60 existed and were deleted. Defense: publish the chain's latest hash to a third-party append-only log periodically; M2 work.

## Failure modes you should expect during real verification

| What you see | What it usually means | What to do |
|---|---|---|
| `sha256sum` mismatch on the WARC | File altered after storage, or path bug | Stop. Capture the disagreement; investigate path. |
| Ed25519 signature verify fails | Wrong public key, or WARC altered after sign | Confirm public key source; recompute WARC hash. |
| TSA verify fails with "wrong root CA" | TSA cert rotated since this run | Fetch historical TSA cert from the TSA's archive page. |
| Hash chain mismatch at row N | Tampering at or before row N | Treat all rows >= N as suspect; trust rows < N only. |
| Wayback Machine 404 on the source URL | Source was wayback-queued but IA hasn't archived yet | Wait 24h and retry; not necessarily a tampering signal. |
| `_chain.jsonl` is missing | Storage layer corruption OR fresh-start tampering | If you saved the genesis hash externally, compare. Otherwise the dossier is uncorroborated. |

## When to redo verification

- Before relying on a dossier for any high-stakes decision (signing a lease, refunding a payment, filing a report).
- After moving the investigation's storage between machines.
- If you suspect the local clock has been tampered with — the TSA token defeats this, but only if you verify it.
- Periodically (monthly?) on dossiers you depend on. Storage corruption is a real and undramatic failure mode.

## What if you don't have time for the full 10 minutes?

Steps 2 and 5 are the cheapest and catch the most common failure mode (post-hoc tampering of either the WARC or the chain). If you only do those two, you've still caught 80% of what the chain protects against. Steps 3, 4, and 6 protect against the harder attacks (impersonation, clock tampering, source repudiation) and are worth doing for any dossier you depend on.
