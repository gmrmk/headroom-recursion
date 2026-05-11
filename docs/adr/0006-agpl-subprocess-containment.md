# ADR-0006: AGPL §13 subprocess containment

- **Status:** accepted
- **Date:** 2026-05-10
- **Deciders:** security, architect, legal review pending
- **Tags:** license, agpl, isolation, ci

## Context

Five tools in the M1/M2 spine are **AGPL-3.0**: GHunt, bbot, social-analyzer, snscrape, TruffleHog. AGPL §13 ("Remote Network Interaction") triggers a source-distribution obligation when the work is *used* over a network interaction by remote users. The legal question is whether dynamic in-process import of an AGPL module makes the importing program "a work based on the Program" under §0, §1, and the §13 network-distribution language.

Counsel's read (and the OSI's published guidance, plus the OCCRP precedent in shipping Aleph): in-process import of AGPL is the **highest-risk** form of integration; subprocess-only integration with a stable IPC contract is the **standard answer** for keeping the host application out of §13's distribution clause. The path is well-trodden: GitLab's Omnibus, NextCloud's external apps, several Mozilla integrations.

OSINT GOBLIN's dashboard itself is licensed AGPL-3.0-or-later (ADR's Consequences section captures the analysis). That is *not* a free pass to in-process import the AGPL OSINT tools — AGPL'd-application + AGPL'd-library does not automatically resolve §13 boundaries cleanly when the licenses are versioned identically across both. The defensible answer regardless of host license is subprocess isolation.

## Decision

The following five modules are **never imported in-process** in any dashboard code:

```
ghunt
bbot
social_analyzer
snscrape
truffleHog
```

Each is invoked via:

```python
result = subprocess.run(
    [tool_binary, "--json"],
    input=json.dumps(payload).encode("utf-8"),
    capture_output=True,
    timeout=300,
    check=True,
)
parsed = json.loads(result.stdout)
```

The contract is **JSON-stdin → JSON-stdout**. Adapters in `adapters/agpl/<tool>/` shell out; adapters in `adapters/foss/<tool>/` may import in-process.

CI enforcement, `.ci/lint-agpl-imports.py`:

```python
AGPL_MODULES = r'\b(ghunt|bbot|social_analyzer|snscrape|truffleHog)\b'
# Reject any `import` or `from` statement matching this regex in
# api/, worker/, adapters/foss/, web/, evidence/, db/
```

The rule runs on every PR; failure blocks merge. The rule is bypassable only by adding a `# agpl-shell-only` comment on the line, which is automatically reviewed by the security working group via CODEOWNERS.

## Consequences

- **Positive.** The dashboard's host-application surface stays out of AGPL §13's network-distribution interpretation, regardless of the dashboard's own license. Reversible if the public-release license decision lands as AGPL and counsel later approves in-process imports.
- **Positive.** Subprocess isolation is a security win independent of license: a malicious AGPL tool update can't read process memory; binary version pinning + checksum in `ops/tool-versions.lock` is a supply-chain control.
- **Negative.** Subprocess startup latency (~100–300ms typical for Python tools). Mitigated by per-investigation tool warm-pool where it matters (`tool_runner` middleware).
- **Negative.** JSON-stdin/stdout limits the contract surface. Streaming partial results (e.g. bbot's long-running scans) requires either NDJSON or a side-channel (Redis pubsub) — documented in `docs/reference/adapter-protocol.md`.
- **Neutral.** The dashboard's **own** release license is AGPL-3.0-or-later (matches the dependency floor; keeps options open). If we later relicense to a more permissive license, this ADR stays in force regardless — §13 is about how we *integrate* AGPL deps, not how we license ourselves.

## References

- `INTEGRATION-SPEC.md` §10 (AGPL subprocess contract)
- `CONSOLIDATED-ROADMAP.md` §1 (license containment)
- `phase3/05-security-compliance.md` §10 (the CI lint regex source)
- AGPL-3.0 §0, §1, §13 (primary regulatory text)
- ADR-0001 (CI lives in this tree)
- ADR-0003 (`tool_runner` enforces the subprocess/in-process branch via the registry)
