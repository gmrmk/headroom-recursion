# Headroom integration modes

[Headroom](https://github.com/headroomlabs-ai/headroom) reversibly compresses tool
outputs, logs, files, and history before they reach the model — "60–95% fewer tokens,
same answers." Because this harness makes many calls (`n`+2 per step, across multiple
tiers), the scratchpad and history it carries forward would otherwise balloon; Headroom
keeps each call small so the recursion stays cheap.

Install:

```bash
pip install "headroom-ai[all]"     # Python 3.10+
```

`headroom-ai` is an **optional** dependency of this project. If it is absent, the loop
runs uncompressed and reports zero savings — nothing breaks.

## Mode 1 — library (default here)

Every outbound message list is passed through `headroom.compress(messages, model=…)`
before it is sent to Claude. This is what `headroom_recursion/headroom.py` does; it
handles both sync and async `compress`, and falls back to the raw messages if
compression errors. The trace records estimated tokens before/after so you can see the
savings (`recurse --json`, field `savings_pct`).

Turn it off per run to A/B the effect:

```bash
recurse --no-headroom "<problem>"
```

## Mode 2 — proxy (transparent, no per-call code)

Run Headroom as a local proxy and point the Anthropic client at it:

```bash
headroom proxy --port 8787
recurse --base-url http://127.0.0.1:8787 --no-headroom "<problem>"
```

Use `--no-headroom` here so compression happens **once** in the proxy rather than also
in library mode. `--base-url` is forwarded to `ClaudeClient(base_url=…)`.

## Mode 3 — MCP server

Headroom can also install as an MCP server exposing `headroom_compress`,
`headroom_retrieve`, and `headroom_stats`:

```bash
headroom mcp install
```

This is orthogonal to the harness — useful when the *agent* (not this loop) wants to
compress or retrieve on demand. `headroom_retrieve` fetches the original text behind any
compressed span, which is how Headroom stays lossless.

## Useful environment variables

- `HF_HUB_OFFLINE=1` — use pre-downloaded compression models (offline / air-gapped).
- `HEADROOM_OUTPUT_SHAPER=1` — also trim output tokens.
- `HEADROOM_OUTPUT_HOLDOUT=0.1` — keep a control group (proxy mode) to measure quality.
- `HEADROOM_TLS_STRICT=0` — relax TLS if the proxy sits behind a corporate MITM.
