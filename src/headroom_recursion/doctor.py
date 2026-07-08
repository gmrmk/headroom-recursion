"""``recurse --doctor`` — the readiness pulse-check.

One table, three severities, exit 0/1. Every check encodes a failure mode that
actually happened (see the risk register in the repo's planning history):
refusal envelopes parsed structurally, per-model canary probes (a ladder tier
can be policy-refused while others work), lean AND lake on PATH (project-mode
verification needs both), three-level Lean readiness, and a network-free
stub-loop smoke that exercises the whole recursion plumbing.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional

OK, WARN, FAIL = "ok", "warn", "fail"

# Deliberately mundane: terse "reply with exactly X" probes have been measured
# to trip per-message safeguards on some tiers that answer real prompts fine —
# a canary must test the transport, not the safeguard's opinion of canaries.
CANARY_PROMPT = "What is two plus two? Answer with only the digit."
CANARY_EXPECTED = "4"


@dataclass
class Check:
    name: str
    level: str  # ok | warn | fail
    detail: str


def _module(name: str) -> bool:
    importlib.invalidate_caches()
    return importlib.util.find_spec(name) is not None


def check_python_deps() -> list[Check]:
    out = []
    for mod, why, missing_level in [
        ("anthropic", "SDK client", WARN),
        ("headroom", "context compression (runs uncompressed without it)", WARN),
        ("openai", "OpenAI-compatible backends", WARN),
        ("lightrag", "LightRAG retrieval (CorpusRetriever needs nothing)", WARN),
    ]:
        present = _module(mod)
        out.append(
            Check(
                f"python: {mod}",
                OK if present else missing_level,
                why if present else f"not installed — {why}",
            )
        )
    return out


def check_cli_transport(
    models: tuple[str, ...],
    *,
    probe: bool,
    timeout_s: float = 120.0,
    runner: Optional[Callable] = None,
) -> list[Check]:
    """CLI presence, then a canary per ladder model through the REAL transport
    parser — this exercises the JSON envelope and refusal detection live."""

    exe = shutil.which("claude")
    if exe is None:
        return [Check("claude CLI", FAIL, "not on PATH — CLI transport unavailable")]
    try:
        ver = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except Exception as exc:
        ver = f"version probe failed: {type(exc).__name__}"
    out = [Check("claude CLI", OK, ver or exe)]
    if not probe:
        return out

    from headroom_recursion.clients import CLITransportClient, TransportRefused

    client = CLITransportClient(attempts=1, timeout_s=timeout_s, runner=runner)
    for model in models:
        try:
            res = client.complete(
                model=model, system="You are a quick arithmetic check.", user=CANARY_PROMPT,
                use_headroom=False,
            )
        except TransportRefused as exc:
            out.append(Check(f"model: {model}", FAIL, f"refused: {str(exc)[:120]}"))
            continue
        except Exception as exc:
            out.append(Check(f"model: {model}", FAIL, f"{type(exc).__name__}: {str(exc)[:120]}"))
            continue
        if res.text.strip() == CANARY_EXPECTED:
            out.append(Check(f"model: {model}", OK, f"exact compliance (${res.cost_usd:.3f})"))
        else:
            out.append(Check(f"model: {model}", WARN, f"answered but not exact: {res.text[:60]!r}"))
    return out


def lean_level(
    *, project_dir: Optional[str] = "lean", timeout_s: float = 300.0,
    runner: Optional[Callable] = None,
) -> tuple[str, str]:
    """Highest working Lean level: 'mathlib' | 'core-lean' | 'none', plus detail."""

    run = runner or subprocess.run
    if shutil.which("lean") is None:
        return "none", "lean not on PATH"
    if shutil.which("lake") is None:
        return "core-lean", "lake not on PATH — project mode (Mathlib) unavailable"
    core = 'theorem doctor_smoke : 1 + 1 = 2 := rfl\n'
    with tempfile.NamedTemporaryFile("w", suffix=".lean", delete=False) as fh:
        fh.write(core)
        core_path = fh.name
    try:
        out = run(["lean", core_path], capture_output=True, text=True, timeout=60,
                  env={"PATH": os.environ.get("PATH", "")})
        if out.returncode != 0:
            return "none", f"core smoke failed: {(out.stderr or out.stdout)[:120]}"
    except Exception as exc:
        return "none", f"core smoke failed: {type(exc).__name__}"
    finally:
        try:
            os.unlink(core_path)
        except OSError:
            pass

    if not project_dir or not os.path.isdir(project_dir):
        return "core-lean", "no lake project dir — Mathlib unavailable"
    with tempfile.NamedTemporaryFile("w", suffix=".lean", delete=False) as fh:
        fh.write("import Mathlib\ntheorem doctor_smoke : 2 + 2 = 4 := by norm_num\n")
        ml_path = fh.name
    try:
        out = run(
            ["lake", "env", "lean", ml_path],
            capture_output=True, text=True, timeout=timeout_s,
            env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
            cwd=project_dir,
        )
        if out.returncode == 0:
            return "mathlib", f"Mathlib smoke compiles (project: {project_dir})"
        return "core-lean", f"Mathlib smoke failed: {(out.stderr or out.stdout)[:120]}"
    except Exception as exc:
        return "core-lean", f"Mathlib smoke failed: {type(exc).__name__}"
    finally:
        try:
            os.unlink(ml_path)
        except OSError:
            pass


def check_lean(**kw) -> list[Check]:
    level, detail = lean_level(**kw)
    sev = {"mathlib": OK, "core-lean": WARN, "none": WARN}[level]
    return [Check(f"lean: level {level}", sev, detail)]


def check_paths(
    *, corpus: Optional[str] = None, writable: tuple[str, ...] = ()
) -> list[Check]:
    out = []
    if corpus:
        try:
            with open(corpus, "r", encoding="utf-8") as fh:
                n = sum(1 for ln in fh if ln.strip() and not ln.lstrip().startswith("#"))
            out.append(Check("corpus", OK, f"{corpus}: {n} entries"))
        except OSError as exc:
            out.append(Check("corpus", FAIL, f"{corpus}: {exc}"))
    for path in writable:
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".doctor-probe")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.unlink(probe)
            out.append(Check(f"writable: {path}", OK, ""))
        except OSError as exc:
            out.append(Check(f"writable: {path}", FAIL, str(exc)))
    return out


def check_stub_loop() -> list[Check]:
    """Network-free end-to-end smoke of the recursion plumbing itself."""

    from headroom_recursion.claude import CallResult
    from headroom_recursion.config import RecurseConfig, Tier
    from headroom_recursion.ladder import recurse

    class _Stub:
        def complete(self, *, model, system, user, **kw):
            if "verifier" in system or "halt_prob" in system:
                return CallResult('{"halt_prob": 1.0, "reason": "stub"}', 10, 10)
            return CallResult("stub output", 10, 10)

    try:
        trace = recurse(
            "doctor smoke", client=_Stub(),
            config=RecurseConfig(ladder=(Tier("stub-model"),), n=1, T=1),
        )
    except Exception as exc:
        return [Check("stub loop", FAIL, f"{type(exc).__name__}: {exc}")]
    if trace.halted and trace.total_calls == 3:
        return [Check("stub loop", OK, "draft → refine → judge plumbing works")]
    return [Check("stub loop", FAIL, f"unexpected trace: {trace.stop_reason}, {trace.total_calls} calls")]


def run_doctor(
    *,
    models: tuple[str, ...] = (),
    probe_models: bool = True,
    lean_project: Optional[str] = "lean",
    lean_timeout_s: float = 300.0,
    corpus: Optional[str] = None,
    writable: tuple[str, ...] = ("runs",),
    cli_runner: Optional[Callable] = None,
    lean_runner: Optional[Callable] = None,
) -> tuple[list[Check], int]:
    checks: list[Check] = []
    checks += check_python_deps()
    checks += check_cli_transport(models, probe=probe_models, runner=cli_runner)
    checks += check_lean(project_dir=lean_project, timeout_s=lean_timeout_s, runner=lean_runner)
    checks += check_paths(corpus=corpus, writable=writable)
    checks += check_stub_loop()
    return checks, (1 if any(c.level == FAIL for c in checks) else 0)


def render(checks: list[Check]) -> str:
    tag = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    width = max(len(c.name) for c in checks)
    return "\n".join(
        f"{tag[c.level]} {c.name.ljust(width)}  {c.detail}".rstrip() for c in checks
    )
