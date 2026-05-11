"""
tools/ci/agpl_import_lint.py — Sora ADR-0002 / Camille §6 AGPL containment lint.

Walks every `.py` under packages/ and apps/ and rejects any `import <AGPL_TOOL>`
or `from <AGPL_TOOL>...import ...` statement.

The wrapper exemption is **path-based, not pattern-based**:
  - Any file under `adapters/<id>/wrapper.py` is exempted.
  - That makes the exemption load-bearing on the directory boundary, not on
    an inline comment a future contributor could spoof.

CLI:
  python tools/ci/agpl_import_lint.py            # lint packages/ + apps/
  python tools/ci/agpl_import_lint.py PATHS...   # lint a fixture set
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

AGPL_FORBIDDEN = {
    "bbot",
    "ghunt",
    "social_analyzer",
    "snscrape",
    "trufflehog",
    "phoneinfoga",
    "onionsearch",
    "ivre",
    "aleph",
    "spiderfoot",
}


def is_wrapper_exempt(path: Path) -> bool:
    """Path-based exemption: adapters/<id>/wrapper.py only.

    Accepts both ``adapters/<id>/wrapper.py`` at the top of a relative scan
    and ``…/adapters/<id>/wrapper.py`` deeper in an absolute path. The
    basename MUST be exactly ``wrapper.py`` — sibling names (``wrapper_real.py``,
    ``wrapper_with_real_import.py``) are NOT exempt.
    """
    parts = path.as_posix().split("/")
    if path.name != "wrapper.py":
        return False
    if len(parts) < 3:
        return False
    # Require: adapters/<some-id>/wrapper.py somewhere in the path.
    for i, part in enumerate(parts):
        if part == "adapters" and i + 2 < len(parts) and parts[i + 2] == "wrapper.py":
            return True
    return False


def lint_file(path: Path) -> list[str]:
    if is_wrapper_exempt(path):
        return []
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (SyntaxError, UnicodeDecodeError):
        return []
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in AGPL_FORBIDDEN:
                    violations.append(
                        f"{path.as_posix()}:{node.lineno}: forbidden import '{alias.name}'"
                        + (f" as '{alias.asname}'" if alias.asname else "")
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in AGPL_FORBIDDEN:
                    for alias in node.names:
                        violations.append(
                            f"{path.as_posix()}:{node.lineno}: forbidden 'from {node.module} "
                            f"import {alias.name}'"
                        )
    return violations


def lint_paths(paths: list[Path]) -> int:
    failed = False
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            files = [root]
        else:
            files = list(root.rglob("*.py"))
        for f in files:
            for msg in lint_file(f):
                print(msg, file=sys.stderr)
                failed = True
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        roots = [Path(a) for a in argv[1:]]
    else:
        roots = [Path("packages"), Path("apps")]
    return lint_paths(roots)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
