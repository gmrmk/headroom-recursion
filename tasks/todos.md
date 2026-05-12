# tasks/todos.md — AI-image detection adapter

**Status:** plan-pending-verification
**Created:** 2026-05-11
**Trigger:** user ask "figure out if the images searched are AI-created" + "Let's align with the CLAUDE.md"

Aligning with CLAUDE.md: plan first, verify before implementing, no laziness, simplicity first, demand elegance, research & reuse mandatory.

---

## Research findings (GitHub-first, completed)

A general-purpose subagent surveyed the FOSS local-inference AI-detector landscape (2026 reality). Key findings:

### State of the field

- Every academic detector with published weights (AIDE / DRCT / NPR / UnivFD / SPAI / SSP / SAFE / C2P-CLIP) collapses to **18–30% on FLUX Dev / Firefly v4 / MJ v7 / Imagen 4** per ICCV 2025 benchmarks. DALL-E 3 hovers at 31%.
- No single FOSS detector breaks 80% accuracy overall in 2026 real-world tests.
- The defensible architecture is **ensemble + provenance + heuristics + human review** — never a single score.

### Practical FOSS options

| Layer | What | Cost | Signal |
|---|---|---|---|
| **A. Provenance + heuristic** | C2PA byte-scan + PNG `parameters` chunk + EXIF anomaly + dimensions | $0, CPU, ~50ms/image | Catches 100% of *lazy* AI-listings; very few false positives when signal fires |
| **B. ML classifier** | `Organika/sdxl-detector` (Swin Transformer, 86.8M params, CC-BY-NC-3.0) via `transformers`, weights cached to `~/.cache/huggingface/`, fully local after first download | ~347 MB disk, ~1-2s/image CPU | Good on SDXL-era; falls off on FLUX/SD3/DALL-E 3 |
| **C. Cloud SaaS** | Sightengine, Hive, Optic | $ + sends image to third party | **Disqualified** per target-data-handling policy (cdc1a92d) |

### License notes

- `transformers` is permitted by `llm-import-lint` (it's a generic ML lib, not LLM provider). The `huggingface_hub` package IS blocked (Inference API); we'd use `transformers` only for local weight load + inference.
- `Organika/sdxl-detector` weights are **CC-BY-NC-3.0** (non-commercial). The user's stated use is personal property-vetting, which is non-commercial. Mixed-license risk: the model weights aren't redistributed with the codebase; user downloads them via `transformers` at runtime. The codebase's Apache-2.0 license isn't compromised.

---

## Plan

### v1: Layer A only — `image_ai_local_detect` adapter

Already drafted (uncommitted) in `apps/workers/.../adapters_image.py`. Heuristic stack:

1. EXIF anomalies (no camera make/model, no datetime, no GPS, generator string in Software/Artist/Copyright)
2. Dimension fingerprints (exact match to known AI default sizes; both axes /64)
3. Filename heuristic (URL contains generator-name marker)
4. PNG chunk scanner (`parameters` / `prompt` / `workflow` tEXt or iTXt keys → near-confirmation)
5. C2PA byte-scan (JUMBF box presence as a fact, not a verdict)

Wire shape: one `image-match` event with `source: ai_local_detect`, `ai_likelihood: none|low|medium|high`, total `score`, `reasons[]`, and per-signal breakdown.

**Cost:** zero new deps, pure-Python, CPU-only, ~50ms/image. Currently ~300 lines.

### v2 (deferred): Layer B — `image_ai_ml_detect` adapter

Subprocess wrapper (empirical venv) invoking `Organika/sdxl-detector` via `transformers`. Gated behind explicit env flag `OSINT_AI_ML_ENABLED=1` so the model weights download only happens with explicit investigator opt-in. Returns raw logit + label + confidence; treat <0.7 as "inconclusive."

**Cost:** ~347 MB one-time disk + ~1-2s/image CPU. New subprocess wrapper + transformers + torch deps in empirical venv only (not the worker venv).

### Workflow integration

- Add `image_ai_local_detect` to **W4.im** (Image OSINT) as the final step
- Add to **W9.pv** (Property Vetting) under the photo-vetting branch
- Surface in cmd-K via single-source catalog (image group)

### Tests

- Synthetic mode wire-shape (event_type, source, ai_likelihood)
- Score-rubric thresholds (score → likelihood mapping)
- Heuristic unit tests (each function in isolation)
- Integration test with a sample PNG that has A1111 parameters chunk (controlled fixture, no external fetch)

### Constraints carried forward

- No third-party upload (per `docs/security/target-data-handling-policy.md`)
- No LLM feeding (per same)
- No audit log (logless contract)
- Honest "this is heuristic, not proof" framing in catalog hint + event payload

---

## Decisions needed from user before implementation

1. **Ship v1 (Layer A only) now and defer v2?**  Or  **ship v1 + v2 together?**
   - v1 alone catches "lazy" AI listings. Good 80/20.
   - v2 adds SDXL-era ML signal but requires `transformers` + `torch` in empirical venv (~3 GB disk for torch+model) and the CC-BY-NC license caveat.

2. **Naming.** `image_ai_local_detect` is descriptive but long. Alternatives: `ai_detect`, `image_ai_heuristic`, `genai_check`. Prefer descriptive — investigators don't grep often.

3. **Should the existing `ai_image_detection` (Sightengine) adapter be deregistered?**  Its docstring now warns about the third-party-upload policy conflict. Keeping it registered means it shows up in cmd-K, which could trip up an investigator.

4. **Where does the rule go for the existing `c2pa_verify` adapter?** It's already in the registry. The new heuristic detector includes a *presence* C2PA byte-scan (doesn't need c2patool installed); `c2pa_verify` is the cryptographic verification path. Keep both? Mark `c2pa_verify` as "deep" / opt-in?

---

## Definition of done

- Adapter committed, registered, in catalog, in W4.im and W9.pv workflows
- Tests cover wire shape + each heuristic + score rubric
- Live smoke against a known AI image (e.g., Stable Diffusion PNG with parameters chunk) returns `ai_likelihood: high`
- Live smoke against a known camera image returns `ai_likelihood: none`
- Docs note that this is heuristic ensemble, not proof
- Reasoning lessons captured in tasks/lessons.md if any correction came up

---

## Lessons (CLAUDE.md §self-improvement)

- **2026-05-11:** "Wait, look on github" — user enforced the GitHub-first research discipline AFTER I'd started building. Future rule: for any new ML / detection / classification feature, dispatch a parallel GitHub-recon subagent IN THE PLAN-FIRST step. Don't start hand-rolling until research has been delivered.
