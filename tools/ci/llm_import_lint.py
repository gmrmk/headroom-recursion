"""
tools/ci/llm_import_lint.py -- target-data-handling policy enforcement.

Per docs/security/target-data-handling-policy.md (user directive
2026-05-11), no LLM provider library may be imported into the worker /
api / packages tree. Target data must NEVER reach an LLM. This lint is
the structural guard that makes the policy unforgeable in code review.

Forbidden modules (LLM provider SDKs + orchestration frameworks that
typically ship LLM calls by default):
  - openai
  - anthropic
  - google.generativeai / google_genai
  - cohere
  - mistralai
  - replicate
  - together (together-ai)
  - groq
  - huggingface_hub  (the Inference API path)
  - ollama  (local LLM but still an LLM)
  - llama_index
  - langchain  (and the langchain_* family)

Two scan layers (same architecture as agpl_import_lint.py):
  1. AST layer: `import X`, `from X import Y` -- zero false positives.
  2. Regex layer: dynamic imports, exec / eval / importlib.import_module.

Exemption is path-based, not pattern-based: any file under
`adapters/<id>/wrapper.py` is exempted. Adapter wrappers run in their
own subprocess + venv, and the subprocess_adapter contract gives us
ample isolation -- but we still discourage LLM use in wrappers. The
exemption exists so the lint doesn't outright forbid future
experimentation; the policy doc remains the binding constraint.

CLI:
  python tools/ci/llm_import_lint.py            # lint packages/ + apps/
  python tools/ci/llm_import_lint.py PATHS...   # lint a fixture set
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

LLM_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "openai",
    "anthropic",
    "google.generativeai",
    "google_genai",
    "cohere",
    "mistralai",
    "replicate",
    "together",
    "groq",
    "huggingface_hub",
    "ollama",
    "llama_index",
    "langchain",
)


def _is_forbidden(module: str) -> bool:
    """True if `module` is forbidden or a submodule of a forbidden root.
    E.g. `langchain` and `langchain_community.tools` both match the
    `langchain` prefix; `openai.types` matches `openai`."""
    if not module:
        return False
    for prefix in LLM_FORBIDDEN_PREFIXES:
        if module == prefix or module.startswith(prefix + ".") or module.startswith(prefix + "_"):
            return True
    return False


def _is_exempt_path(path: Path) -> bool:
    """Wrapper subprocesses live under adapters/<id>/wrapper.py and run
    in their own venv. Exempt from this lint (policy still applies)."""
    parts = path.resolve().parts
    return "adapters" in parts and any(p == "wrapper.py" for p in parts)


def _scan_ast(path: Path) -> list[tuple[int, str, str]]:
    """Return (lineno, kind, module) for every forbidden static import."""
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        sys.stderr.write(f"warn: skipping unreadable {path}: {exc}\n")
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        sys.stderr.write(f"warn: syntax-error in {path}: {exc}\n")
        return []
    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden(alias.name):
                    hits.append((node.lineno, "import", alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_forbidden(module):
                hits.append((node.lineno, "from-import", module))
    return hits


_DYNAMIC_RES = (
    re.compile(r"""__import__\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""importlib\.import_module\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""(?:exec|eval)\(\s*['"]([^'"]*import[^'"]+)['"]"""),
)


def _scan_dynamic(path: Path) -> list[tuple[int, str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    hits: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if "# llm-lint: dynamic-ok" in line:
            continue
        for pat in _DYNAMIC_RES:
            for m in pat.finditer(line):
                captured = m.group(1)
                # exec/eval may capture a snippet; extract module names.
                for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", captured):
                    if _is_forbidden(token):
                        hits.append((lineno, "dynamic", token))
    return hits


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    if _is_exempt_path(path):
        return []
    return _scan_ast(path) + _scan_dynamic(path)


def _walk(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            files.append(root)
        elif root.is_dir():
            for p in root.rglob("*.py"):
                # Skip third-party + cache trees.
                parts = p.parts
                if any(part in {".venv", "venv", "node_modules", "__pycache__"} for part in parts):
                    continue
                files.append(p)
    return files


def main(argv: list[str]) -> int:
    if argv:
        roots = [Path(a) for a in argv]
    else:
        repo_root = Path(__file__).resolve().parents[2]
        roots = [repo_root / "packages", repo_root / "apps"]

    files = _walk(roots)
    all_hits: list[tuple[Path, int, str, str]] = []
    for f in files:
        for hit in _scan_file(f):
            all_hits.append((f, *hit))

    if not all_hits:
        return 0

    print("LLM import lint FAILED -- target-data-handling-policy.md violation:")
    for path, lineno, kind, module in all_hits:
        print(f"  {path}:{lineno}  {kind}: {module}")
    print()
    print("LLM providers / orchestrators must not be imported into worker/api/packages.")
    print("Target data must never reach an LLM (see docs/security/target-data-handling-policy.md).")
    print("If you have a legitimate non-target-data use case, discuss in code review first.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
