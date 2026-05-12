# Target Data Handling Policy

**Status:** binding, project-wide
**Effective:** 2026-05-11
**Authority:** user directive

This policy governs how target data — emails, phones, names, addresses, photos, and any other identifier of an investigative subject — is handled across the osint-goblin codebase. It overrides any conflicting recommendation from any persona-driven review.

## Two binding directives

### 1. Logless: no target data persists outside the one-shot report

Target data may flow through the event stream to the investigator-facing dossier (the "one-shot report" — the SSE stream, the InMemoryStore or m1 Postgres store, the dossier export). It must **not** flow to any *secondary* persistence layer:

- No audit logs of who was queried
- No analytics or telemetry that contains identifiers
- No usage logs that record target values
- No side-channel storage that could outlive the investigation

The investigator deletes the investigation when done; nothing remains.

### 2. No LLM feeding

Target data — and any data derived from it — must **never** be sent to a Large Language Model. This includes:

- No LLM-based adapters (no "summarize this dossier via OpenAI", no "classify this person via Claude")
- No LLM-based features in the worker pipeline
- No LLM-based UI affordances that receive target data as context
- No telemetry that ships target-bearing payloads to any AI service

The reason: LLM providers retain prompts, train on them, and process them through systems we don't control. Even providers that say they don't train on inputs may log them for safety review or be subpoenaed.

**Allowed:** LLMs that operate on *non-target* data are fine — e.g., the cmd-K ranker is a deterministic scoring function, not an LLM, but if it ever needed to be: an LLM ranking *adapter names + investigator query strings* doesn't violate this policy because no target data is in the context.

**Disallowed:** any feature where the LLM context includes an emailaddress, phone, name, address, photo, breach data, or any other field that originated from or names a target.

## Implementation

### What we actively do

- The `partial_recovery` wrapper never writes target data to disk (the rate-limit lockfile contains only platform-name + timestamp; verified by test).
- The Hudson Rock + IntelBase adapters strip credential fields recursively via `_redact_credentials` before emit.
- The partial-pivot adapters redact partial-value strings by default; raw values opt-in via `OSINT_PARTIAL_KEEP_VALUES=1` for live review only.
- Zero adapters in the registry use any LLM API as of 2026-05-11.

### What we don't do (and won't add)

- No `openai_*` / `anthropic_*` / `gemini_*` / `together_*` adapters
- No "AI dossier summarizer"
- No "AI verdict generator" that consumes the event stream — the existing `_synthesize_verdict` in `tools/dev/smoke-w11-em.py` is a pure rule-based reducer over event counts, NOT an LLM call.
- No analytics, telemetry, or crash reporting that could include investigation payloads.

### What investigators should not do

- Do not paste investigation results into ChatGPT / Claude / Gemini / any LLM for summarization.
- Do not run `/graphify` or any other LLM-using tool over data directories that contain investigation events.
- Do not commit investigation outputs to public repositories.

### Enforcement

Future adapters or features that would violate either directive should be rejected at code review. The CI's existing AGPL-import lint can be extended to block known LLM provider packages if needed; for now the policy lives in this doc + the wrapper docstrings + reviewer judgment.

## Audit trail (for this policy itself, not for target data)

| Date | Change | Reason |
|---|---|---|
| 2026-05-11 | Policy created | User directive: "Anyone's data fed through this should never be stored, only used for the one shot report" + "never feeding it to LLMs and stuff" |
| 2026-05-11 | Removed Naomi #4 audit log from partial_recovery wrapper | Same user directive |
