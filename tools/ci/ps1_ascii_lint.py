"""tools/ci/ps1_ascii_lint.py - reject non-ASCII in .ps1 files.

PowerShell on Win11 reads .ps1 files via the OEM/cp1252 codepage unless a UTF-8
BOM is present. Smart-quotes, em-dashes (U+2014), section-signs (U+00A7), etc.
corrupt parsing and cascade into 'Unexpected token' errors that don't point at
the real cause.

This lint forces ASCII-only content in .ps1 files. Priya WI-0125b.
"""

from __future__ import annotations

import sys
from pathlib import Path


def lint_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [f"{path.as_posix()}: not valid UTF-8 ({e})"]
    violations: list[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for j, ch in enumerate(line):
            if ord(ch) > 127:
                violations.append(
                    f"{path.as_posix()}:{i}:{j + 1}: non-ASCII char "
                    f"U+{ord(ch):04X} {ch!r} (PowerShell parses .ps1 as cp1252; "
                    f"replace with ASCII)"
                )
                break  # one error per line is enough
    return violations


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv[1:]] if len(argv) > 1 else list(Path(".").rglob("*.ps1"))
    failed = False
    for p in paths:
        if not p.exists() or not p.is_file():
            continue
        for msg in lint_file(p):
            print(msg, file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
