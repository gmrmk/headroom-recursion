"""
tools/ci/module_dag_lint.py — enforces Sora §4 dependency DAG via AST.

Layers (leaf -> trunk):
  L0: schemas, forensics                 (no project-internal imports)
  L1: db, ftm, opsec, fetcher            (may import L0 only; peer rule: no L1<->L1)
  L2: adapters, attestation              (may import L0 + specific L1; no L2<->L2)
  L3: evidence_pipeline                  (may import L0+L1+L2)
  L4: apps/api, apps/workers             (may import all packages)

The lint walks every `.py` under packages/ and apps/, parses with `ast`, and
for each project-internal import (`osint_goblin_*`) asserts the edge is in
ALLOWED[current_package].

Exit 0 on clean, 1 on violation. Violations are printed to stderr.

CLI:
  python tools/ci/module_dag_lint.py            # lint packages/ + apps/
  python tools/ci/module_dag_lint.py PATHS...   # lint a fixture set
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# The allowed-edges table. Mirror of Sora §4 + ADR-0002.
ALLOWED: dict[str, set[str]] = {
    "osint_goblin_schemas": set(),
    "osint_goblin_forensics": set(),  # stdlib + cryptography only, project-internal=zero
    "osint_goblin_db": {"osint_goblin_schemas"},
    "osint_goblin_ftm": {"osint_goblin_schemas"},
    "osint_goblin_opsec": {"osint_goblin_schemas"},
    "osint_goblin_fetcher": {"osint_goblin_schemas"},
    "osint_goblin_adapters": {"osint_goblin_schemas", "osint_goblin_fetcher"},
    "osint_goblin_attestation": {
        "osint_goblin_schemas",
        "osint_goblin_db",
        "osint_goblin_forensics",
    },
    "osint_goblin_evidence_pipeline": {
        "osint_goblin_schemas",
        "osint_goblin_db",
        "osint_goblin_ftm",
        "osint_goblin_fetcher",
        "osint_goblin_forensics",
        "osint_goblin_opsec",
        "osint_goblin_adapters",
        "osint_goblin_attestation",
    },
    # Apps may import any package. They are L4 / trunk-consumers, not trunk.
    "osint_goblin_api": {
        "osint_goblin_schemas",
        "osint_goblin_db",
        "osint_goblin_ftm",
        "osint_goblin_opsec",
        "osint_goblin_fetcher",
        "osint_goblin_adapters",
        "osint_goblin_attestation",
        "osint_goblin_evidence_pipeline",
        "osint_goblin_forensics",
    },
    "osint_goblin_workers": {
        "osint_goblin_schemas",
        "osint_goblin_db",
        "osint_goblin_ftm",
        "osint_goblin_opsec",
        "osint_goblin_fetcher",
        "osint_goblin_adapters",
        "osint_goblin_attestation",
        "osint_goblin_evidence_pipeline",
        "osint_goblin_forensics",
    },
}

ALL_PROJECT_PACKAGES = set(ALLOWED.keys())


def package_of(path: Path) -> str | None:
    """Return the top-level project package name a file belongs to, or None."""
    parts = path.as_posix().split("/")
    # packages/<name>/src/<name>/...  -> <name>
    if "packages" in parts:
        i = parts.index("packages")
        if i + 1 < len(parts):
            return parts[i + 1]
    # apps/<name>/src/<inner>/...     -> <inner>
    if "apps" in parts:
        i = parts.index("apps")
        # walk to /src/<inner>/
        if "src" in parts[i:]:
            j = parts.index("src", i)
            if j + 1 < len(parts):
                return parts[j + 1]
    return None


def project_imports(path: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, imported_package_top_level) for project-internal imports."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in ALL_PROJECT_PACKAGES:
                    out.append((node.lineno, top))
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in ALL_PROJECT_PACKAGES:
                out.append((node.lineno, top))
    return out


def lint_file(path: Path) -> list[str]:
    pkg = package_of(path)
    if pkg is None or pkg not in ALLOWED:
        return []
    allowed = ALLOWED[pkg]
    violations = []
    for lineno, imp in project_imports(path):
        if imp == pkg:
            continue  # same-package self import is fine
        if imp not in allowed:
            violations.append(
                f"{path.as_posix()}:{lineno}: forbidden edge {pkg} -> {imp} "
                f"(allowed: {sorted(allowed) or '∅'})"
            )
    return violations


def lint_paths(paths: list[Path]) -> int:
    failed = False
    for root in paths:
        if not root.exists():
            continue
        files = [root] if root.is_file() else list(root.rglob("*.py"))
        for f in files:
            for msg in lint_file(f):
                print(msg, file=sys.stderr)
                failed = True
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] if len(argv) > 1 else [Path("packages"), Path("apps")]
    return lint_paths(roots)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
