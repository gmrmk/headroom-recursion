"""Rubric-coverage lint for dossier-section severity_basis citations.

Per ADR-0026 (wave-4 Margaret roadmap §4). Walks the projection layer
(`apps/web/src/lib/dossier-shape.ts`) for every `severity_basis: "matrix:<id>"`
string literal and asserts each ``<id>`` exists in the ``RUBRIC`` object in
`apps/web/src/lib/severity-rubric.ts`.

The two surfaces are coupled by a string convention, not by an import edge
(Sora wave-4 graphify finding: communities 23 and 27 have no direct edge).
Missing keys return ``undefined`` at runtime — this lint catches the drift
at CI time instead. Mechanism mirrors ADR-0022 (import-linter).

Also scans `apps/web/src/lib/breach-synthesis.ts` for any inline string
literals, even though that file currently uses ``severityBasisRef()``
programmatically. Future literals there would still need rubric coverage.

CLI:
  python tools/ci/rubric_coverage_lint.py
  python tools/ci/rubric_coverage_lint.py CITATION_FILES... --rubric RUBRIC_FILE

Exit 0 on clean, 1 on missing rubric coverage. Violations to stderr.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUBRIC = REPO_ROOT / "apps" / "web" / "src" / "lib" / "severity-rubric.ts"
DEFAULT_CITATION_FILES = [
    REPO_ROOT / "apps" / "web" / "src" / "lib" / "dossier-shape.ts",
    REPO_ROOT / "apps" / "web" / "src" / "lib" / "breach-synthesis.ts",
]

# Match `severity_basis: "matrix:<ID>"` — single or double quotes.
# IDs are SCREAMING_SNAKE_CASE: leading uppercase letter, then upper / digit / underscore.
SEV_BASIS_RE = re.compile(r"""severity_basis\s*:\s*["']matrix:([A-Z][A-Z0-9_]*)["']""")

# Match a top-level RUBRIC entry: `  PV_FOO_BAR: {` at the start of a line.
# The `RUBRIC` object uses two-space indentation for top-level keys; nested
# entries (`    id: "..."`) start with four spaces AND have lowercase keys,
# so they will not match this pattern.
RUBRIC_ENTRY_RE = re.compile(r"^  ([A-Z][A-Z0-9_]*)\s*:\s*\{", re.MULTILINE)


def collect_rubric_ids(rubric_path: Path) -> set[str]:
    """Return the set of top-level RUBRIC keys defined in severity-rubric.ts."""
    text = rubric_path.read_text(encoding="utf-8")
    return set(RUBRIC_ENTRY_RE.findall(text))


def collect_citations(file_path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, rubric_id), ...] for every severity_basis matrix:<id>
    literal in ``file_path``. Missing files contribute zero citations."""
    if not file_path.exists():
        return []
    text = file_path.read_text(encoding="utf-8")
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        for m in SEV_BASIS_RE.finditer(raw):
            out.append((lineno, m.group(1)))
    return out


def lint_paths(citation_files: list[Path], rubric_path: Path) -> int:
    if not rubric_path.exists():
        print(
            f"rubric_coverage_lint: rubric file not found: {rubric_path.as_posix()}",
            file=sys.stderr,
        )
        return 1
    rubric_ids = collect_rubric_ids(rubric_path)
    if not rubric_ids:
        print(
            f"rubric_coverage_lint: parsed zero RUBRIC entries from "
            f"{rubric_path.as_posix()}; check RUBRIC_ENTRY_RE pattern",
            file=sys.stderr,
        )
        return 1

    failed = False
    total_citations = 0
    for cf in citation_files:
        for lineno, rubric_id in collect_citations(cf):
            total_citations += 1
            if rubric_id not in rubric_ids:
                print(
                    f"{cf.as_posix()}:{lineno}: severity_basis cites "
                    f"'matrix:{rubric_id}' but no such entry exists in "
                    f"{rubric_path.as_posix()} RUBRIC",
                    file=sys.stderr,
                )
                failed = True

    if failed:
        return 1
    print(
        f"rubric_coverage_lint: OK "
        f"({total_citations} citation{'s' if total_citations != 1 else ''} / "
        f"{len(rubric_ids)} rubric entries)"
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="ADR-0026 dossier-section rubric-coverage lint.")
    parser.add_argument(
        "citation_files",
        nargs="*",
        type=Path,
        help="TS files to scan for severity_basis literals "
        "(default: dossier-shape.ts + breach-synthesis.ts).",
    )
    parser.add_argument(
        "--rubric",
        type=Path,
        default=DEFAULT_RUBRIC,
        help="Path to severity-rubric.ts (default: apps/web/src/lib/severity-rubric.ts).",
    )
    args = parser.parse_args(argv[1:])
    citation_files = args.citation_files or DEFAULT_CITATION_FILES
    return lint_paths(citation_files, args.rubric)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
