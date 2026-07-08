#!/usr/bin/env python3
"""Independent re-verification of persisted Lean artifacts (Tier 1 + Tier 2).

For every ``runs/lean/*.lean`` artifact:

  compile   lake env lean -o          (our toolchain elaborates + kernel-checks)
  tier 1    leanchecker --fresh       (kernel replay from a clean environment,
                                       distributed with the pinned toolchain -
                                       kills elaborator/metaprogram circumvention)
  tier 2    lean4export -- <decls>    (serialize the named theorems' transitive
                                       closure to Lean's specified NDJSON format)
            nanoda_bin                (re-typecheck in an INDEPENDENTLY
                                       IMPLEMENTED Rust kernel, axiom whitelist
                                       enforced: propext/Classical.choice/Quot.sound)

Fail-closed rules (each one earned): an empty export is a FAILURE, not a pass
(a vacuous "checked 0 declarations" accept was observed live); nanoda must
report checking >= 1 declaration AND exit 0; any stage error fails the artifact.

Outputs: runs/verify/report.json + the .ndjson exports (the re-checkable
objects of record - a third party needs only these files and any conforming
checker, not our toolchain).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEAN_PROJECT = os.path.join(REPO, "lean")
ARTIFACTS = os.path.join(REPO, "runs", "lean")
OUT_DIR = os.path.join(REPO, "runs", "verify")
STAGE = os.path.join(LEAN_PROJECT, ".artifacts")

LEAN4EXPORT = os.environ.get("LEAN4EXPORT", "/root/verify/lean4export/.lake/build/bin/lean4export")
NANODA = os.environ.get("NANODA", "/root/verify/nanoda_lib/target/release/nanoda_bin")
PERMITTED_AXIOMS = ["propext", "Classical.choice", "Quot.sound"]

_DECL_RE = re.compile(r"^\s*(?:theorem|lemma)\s+([A-Za-z_][A-Za-z_0-9.']*)", re.MULTILINE)
_CHECKED_RE = re.compile(r"Checked (\d+) declarations with no typechecker errors")


def run(cmd, *, cwd=LEAN_PROJECT, timeout=600, **kw):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, **kw)


def lake_env(shell_cmd: str, *, timeout=600):
    return run(["lake", "env", "sh", "-c", f'LEAN_PATH="$LEAN_PATH:.artifacts" {shell_cmd}'],
               timeout=timeout)


def verify(path: str) -> dict:
    src = open(path, encoding="utf-8").read()
    digest = hashlib.sha256(src.encode()).hexdigest()
    mod = "A" + digest[:12]
    decls = _DECL_RE.findall(src)
    rec = {"artifact": os.path.basename(path), "sha256": digest, "module": mod,
           "decls": decls, "compile": False, "tier1_leanchecker": False,
           "tier2_nanoda": False, "decls_checked": 0, "error": ""}

    def fail(stage, detail):
        rec["error"] = f"{stage}: {detail.strip()[:400]}"
        return rec

    if not decls:
        return fail("scan", "no top-level theorem/lemma declarations found")

    os.makedirs(STAGE, exist_ok=True)
    staged = os.path.join(STAGE, f"{mod}.lean")
    with open(staged, "w", encoding="utf-8") as fh:
        fh.write(src)

    # compile (emit olean so the replay/export stages can resolve the module)
    out = lake_env(f"lean .artifacts/{mod}.lean -o .artifacts/{mod}.olean")
    if out.returncode != 0:
        return fail("compile", out.stderr or out.stdout)
    rec["compile"] = True

    # tier 1: kernel replay. --fresh re-replays the WHOLE transitive environment
    # into a clean one - right for import-free artifacts, but hours of work for
    # `import Mathlib` ones (every Mathlib constant). Those get an incremental
    # replay of the module itself; their actually-used Mathlib dependencies are
    # still independently re-checked by tier 2's export closure. The report
    # records which depth ran - the claim never overstates.
    fresh = "import" not in src
    rec["tier1_mode"] = "fresh" if fresh else "incremental"
    out = lake_env(f"leanchecker {'--fresh ' if fresh else ''}{mod}", timeout=1200)
    if out.returncode != 0:
        return fail("leanchecker", out.stderr or out.stdout)
    rec["tier1_leanchecker"] = True

    # tier 2: spec'd export of the named decls' closure ...
    ndjson = os.path.join(OUT_DIR, f"{mod}.ndjson")
    out = lake_env(f"{LEAN4EXPORT} {mod} -- {' '.join(decls)}", timeout=1800)
    if out.returncode != 0:
        return fail("lean4export", out.stderr or out.stdout)
    if not out.stdout.strip():
        return fail("lean4export", "EMPTY export - refusing the vacuous pass")
    with open(ndjson, "w", encoding="utf-8") as fh:
        fh.write(out.stdout)
    rec["export_sha256"] = hashlib.sha256(out.stdout.encode()).hexdigest()

    # ... re-typechecked by the independent Rust kernel
    cfg = os.path.join(STAGE, f"{mod}.nanoda.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({"export_file_path": ndjson, "use_stdin": False,
                   "permitted_axioms": PERMITTED_AXIOMS,
                   "unpermitted_axiom_hard_error": True,
                   "nat_extension": True, "string_extension": True,
                   "print_success_message": True}, fh)
    out = run([NANODA, cfg], timeout=1800)
    m = _CHECKED_RE.search(out.stdout + out.stderr)
    checked = int(m.group(1)) if m else 0
    if out.returncode != 0:
        return fail("nanoda", out.stderr or out.stdout)
    if checked < 1:
        return fail("nanoda", f"checked {checked} declarations - refusing the vacuous pass")
    rec["tier2_nanoda"] = True
    rec["decls_checked"] = checked
    return rec


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = sorted(
        os.path.join(ARTIFACTS, f) for f in os.listdir(ARTIFACTS) if f.endswith(".lean")
    ) if os.path.isdir(ARTIFACTS) else []
    if not paths:
        print("no artifacts under runs/lean/ - nothing to verify")
        return 0

    results = [verify(p) for p in paths]
    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "toolchain": open(os.path.join(LEAN_PROJECT, "lean-toolchain")).read().strip(),
        "checkers": {
            "leanchecker": "distributed with the pinned toolchain (kernel replay, --fresh)",
            "lean4export": "leanprover/lean4export @ v4.31.0 (NDJSON format 3.1.0)",
            "nanoda": "ammkrn/nanoda_lib @ master f58f2f6 (independent Rust kernel)",
        },
        "permitted_axioms": PERMITTED_AXIOMS,
        "artifacts": results,
    }
    with open(os.path.join(OUT_DIR, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    ok = True
    for r in results:
        verdict = "VERIFIED(t1+t2)" if r["tier1_leanchecker"] and r["tier2_nanoda"] else "FAILED"
        ok = ok and verdict.startswith("VERIFIED")
        print(f"{verdict:16} {r['artifact']:26} decls={','.join(r['decls'])}"
              f" checked={r['decls_checked']} {r['error']}")
    print(f"report: {os.path.join(OUT_DIR, 'report.json')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
