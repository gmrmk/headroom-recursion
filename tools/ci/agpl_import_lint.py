"""
tools/ci/agpl_import_lint.py -- Sora ADR-0002 / Camille section 6 AGPL containment lint.

Walks every `.py` under packages/ and apps/ and rejects any AGPL-flavored import,
in both static and dynamic forms.

Two scan layers:
  1. AST layer (static imports): `import X`, `from X import Y`, `import X as Z`.
     False positives near zero; false negatives possible on dynamic patterns.
  2. Regex layer (dynamic imports, Camille P1 phase6 2026-05-11):
       __import__("X")
       importlib.import_module("X")
       exec("import X" / "from X import ...")
       eval("__import__('X')" / ...)
     False positives possible on docstrings/strings containing the names; suppress
     line-by-line with `# agpl-lint: dynamic-ok`. The static layer is not
     suppressible -- it has zero false positives.

The wrapper exemption is **path-based, not pattern-based**:
  - Any file under `adapters/<id>/wrapper.py` is exempted from BOTH layers.
  - Exemption is load-bearing on the directory boundary, not on an inline
    comment a future contributor could spoof.

Parse-error handling (Camille P1):
  - A file that fails to parse (UnicodeDecodeError / SyntaxError) is skipped
    BUT a warning is emitted to stderr naming the file. The silent-skip was
    a known exploit surface (hostile contributor inserts a BOM-mangled file
    containing `import maigret`).

CLI:
  python tools/ci/agpl_import_lint.py            # lint packages/ + apps/
  python tools/ci/agpl_import_lint.py PATHS...   # lint a fixture set
"""

from __future__ import annotations

import ast
import re
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

# Per-line suppression marker for the dynamic-regex layer only.
DYNAMIC_OK_MARKER = "agpl-lint: dynamic-ok"


def is_wrapper_exempt(path: Path) -> bool:
    """Path-based exemption: adapters/<id>/wrapper.py only.

    Accepts both ``adapters/<id>/wrapper.py`` at the top of a relative scan
    and ``.../adapters/<id>/wrapper.py`` deeper in an absolute path. The
    basename MUST be exactly ``wrapper.py`` -- sibling names (``wrapper_real.py``,
    ``wrapper_with_real_import.py``) are NOT exempt.
    """
    parts = path.as_posix().split("/")
    if path.name != "wrapper.py":
        return False
    if len(parts) < 3:
        return False
    for i, part in enumerate(parts):
        if part == "adapters" and i + 2 < len(parts) and parts[i + 2] == "wrapper.py":
            return True
    return False


def _ast_violations(path: Path, tree: ast.Module) -> list[str]:
    """Static-import layer: walk Import / ImportFrom nodes."""
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
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".")[0]
            if top in AGPL_FORBIDDEN:
                for alias in node.names:
                    violations.append(
                        f"{path.as_posix()}:{node.lineno}: forbidden 'from {node.module} "
                        f"import {alias.name}'"
                    )
    return violations


# Pre-compile dynamic-import patterns once, parameterized by AGPL module set.
def _build_dynamic_patterns() -> list[tuple[re.Pattern[str], str]]:
    """Return [(compiled regex, human-name), ...] for the dynamic-scan layer.

    Each pattern matches a dynamic-import call referencing an AGPL module name
    inside a string literal. We require the surrounding call shape to reduce
    false positives -- a bare mention of `ghunt` in prose does not trigger.
    """
    alt = "|".join(re.escape(m) for m in sorted(AGPL_FORBIDDEN))
    name_alt = f"(?:{alt})"
    quoted = rf"""['"]\s*{name_alt}(?:\.[A-Za-z_][\w.]*)?\s*['"]"""

    patterns: list[tuple[re.Pattern[str], str]] = [
        (re.compile(rf"\b__import__\s*\(\s*{quoted}"), "__import__(...)"),
        (
            re.compile(rf"\bimportlib\.import_module\s*\(\s*{quoted}"),
            "importlib.import_module(...)",
        ),
        (
            re.compile(r"\b(?:exec|eval)\s*\(\s*['\"][^'\"]*(?:import\s+|__import__\s*\()"),
            "exec/eval(import-string)",
        ),
    ]
    return patterns


_DYNAMIC_PATTERNS = _build_dynamic_patterns()


def _dynamic_violations(path: Path, text: str) -> list[str]:
    """Regex layer: catch __import__ / importlib.import_module / exec / eval.

    Per-line suppression via `# agpl-lint: dynamic-ok` on the same line.
    """
    violations: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if DYNAMIC_OK_MARKER in raw:
            continue
        for regex, label in _DYNAMIC_PATTERNS:
            m = regex.search(raw)
            if m:
                # Identify the AGPL module name. First try the matched span;
                # for exec/eval the match doesn't fully cover the module name
                # (it cuts off after `import `), so fall back to scanning the
                # whole line for any AGPL name.
                matched_text = m.group(0)
                referenced = next(
                    (mod for mod in AGPL_FORBIDDEN if mod in matched_text),
                    None,
                )
                if referenced is None:
                    referenced = next(
                        (mod for mod in AGPL_FORBIDDEN if mod in raw),
                        "(unknown)",
                    )
                violations.append(
                    f"{path.as_posix()}:{lineno}: forbidden dynamic import "
                    f"of '{referenced}' via {label}"
                )
                break  # one violation per line is enough
    return violations


def lint_file(path: Path) -> list[str]:
    if is_wrapper_exempt(path):
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(
            f"warning: {path.as_posix()}: skipped due to UnicodeDecodeError ({exc.reason}); "
            f"file is unparseable as utf-8 and was not scanned",
            file=sys.stderr,
        )
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        print(
            f"warning: {path.as_posix()}: skipped due to SyntaxError at line "
            f"{exc.lineno}; file was not scanned for AGPL imports",
            file=sys.stderr,
        )
        # Still run the regex layer on a syntactically-invalid file -- the
        # regex doesn't need a parseable AST and may still catch dynamic
        # patterns in salvageable text.
        return _dynamic_violations(path, text)

    return _ast_violations(path, tree) + _dynamic_violations(path, text)


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
