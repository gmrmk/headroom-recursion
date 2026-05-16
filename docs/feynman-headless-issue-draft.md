# Draft: GitHub issue for `getcompanion-ai/feynman`

**To file at:** https://github.com/getcompanion-ai/feynman/issues/new

---

## Title

`feynman --prompt "..."` hangs silently on Windows when stdin is not a TTY (0.2.52)

## Body

### Environment

- **OS:** Windows 11 Pro 10.0.26200
- **Shell:** PowerShell 5.1 (Windows PowerShell)
- **Feynman:** 0.2.52-win32-x64 (bundled Node runtime)
- **Install method:** `irm https://feynman.is/install.ps1 | iex` (default installer, latest as of 2026-05-12)
- **Auth:** Anthropic OAuth flow completed successfully, 23 authenticated models
- **`feynman doctor` output:** all green (alphaXiv ok, pandoc ok, pi runtime ok, browser preview ok, default model `anthropic/claude-opus-4-6` valid)

### Reproduction

Three separate invocations all hang silently with **zero stdout/stderr output** and **idle CPU usage** (under 5 seconds of CPU total over 20+ minutes of wallclock time) when stdin is not attached to a TTY:

```powershell
# Form 1: --prompt flag with output redirect
$env:NO_COLOR='1'
feynman --prompt "/deepresearch <topic>" --new-session --session-dir <path> --thinking medium 2>&1 | Out-File <out> -Encoding utf8
# Hangs ~44 min, 0 bytes output

# Form 2: --prompt with trivial input, no redirect
$env:NO_COLOR='1'
feynman --prompt "Reply with exactly 'pong'" --thinking off
# Hangs ~8 min, 0 bytes output

# Form 3: stdin pipe
"Reply with exactly the word pong" | feynman --thinking off
# Hangs ~20 min, 0 bytes output

# Form 4: with CI=1, NO_COLOR=1, TERM=dumb (common headless-mode env vars)
$env:CI='1'; $env:NO_COLOR='1'; $env:TERM='dumb'
feynman --prompt "Reply with just 'pong'" --thinking off
# Hangs ~11 min, 0 bytes output
```

In each case:
- Two Feynman node processes spawn on schedule (the main bundle + a Pi runtime subprocess).
- Memory rises to ~100-120 MB then plateaus.
- CPU jumps once during startup (~2-3s each) then drops to idle.
- Session-dir specified via `--new-session --session-dir <path>` remains empty.
- The processes never emit any stdout/stderr after the initial echo of the user's command.

### Working case

The same binary works perfectly **in interactive mode**:
- `feynman model login anthropic` — OAuth flow completes in a real Windows PowerShell window.
- `feynman` (no args) — opens the REPL successfully.
- `feynman doctor` — emits its diagnostic output cleanly.
- `feynman status` — emits its status output cleanly.

So the issue is specifically with non-TTY (headless / programmatic) invocation paths.

### Expected behavior

`feynman --prompt "..."` should run one prompt and exit (per `feynman help`). It should be safe to call from CI, scripts, agent harnesses, or any context where stdin is a pipe rather than a TTY.

### Suspected cause

The Pi runtime appears to gate on a TTY detection check (likely via `process.stdin.isTTY`) and waits indefinitely for interactive input that will never arrive when invoked headlessly. Common Node libraries that do this: `inquirer`, `enquirer`, `prompts`, `@clack/prompts`.

### Suggested fixes

1. **Honor `CI` env var** — if `process.env.CI` is set, skip TTY detection and proceed in pure non-interactive mode.
2. **Honor `--prompt` flag fully** — when `--prompt` is set, treat the run as headless by definition; never await interactive input.
3. **Fail fast on TTY check** — if a TTY is required and one is not attached, error out within 5 seconds with a clear message ("This command requires an interactive terminal; rerun without redirecting stdin / outside a CI environment") rather than hanging silently.

### Related ecosystem context

This bug blocks programmatic use of Feynman from:
- Claude Code / Cursor / Codex agent harnesses (any IDE-embedded AI shell)
- GitHub Actions / GitLab CI
- Cron / scheduled research tasks
- Multi-agent orchestration where Feynman would be one node

Workaround in progress: a Windows-side `pywinpty` (ConPTY) wrapper that emulates a TTY around `feynman.cmd`. Doable but adds dependency and brittleness.

### `pi-tui` path-layout note (separate observation)

During upgrade troubleshooting we also hit a separate bug: `pi-subagents` at `npm-global\node_modules\pi-subagents\` cannot resolve `@earendil-works\pi-tui` which is installed at `npm-global\lib\node_modules\@earendil-works\pi-tui\`. Two parallel `node_modules` trees on Windows. Copying `@earendil-works\` up one level resolves it. May be worth a separate issue, or post-install cleanup in the installer script.

---

**Reporter note:** happy to provide additional logs or run any diagnostic the team wants. The hang reproduces 100% on `feynman --prompt` here.
