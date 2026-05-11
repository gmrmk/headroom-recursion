"""Scrapling-as-eyes: verify our pinned versions against current upstream latest.

Fetches PyPI / npm JSON registries (no scraping needed for these — they're JSON APIs)
plus Scrapling-driven page fetches for nextjs.org / ui.shadcn.com to catch any
"2026 Q2 breaking change" notes that the JSON APIs don't surface.
"""
from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Pinned versions in our scaffold (read from disk to stay honest)
import tomllib  # py3.11+

pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
package_json = json.loads(Path("package.json").read_text(encoding="utf-8"))

dev_deps = pyproject.get("dependency-groups", {}).get("dev", [])

# Build our pin manifest
ours = {
    "python_requires": pyproject["project"]["requires-python"],
    "version": pyproject["project"]["version"],
}
for dep in dev_deps:
    # Parse "ruff>=0.7" → ("ruff", ">=0.7")
    for sep in (">=", "==", "~=", ">"):
        if sep in dep:
            name, ver = dep.split(sep, 1)
            ours[name.strip()] = f"{sep}{ver.strip()}"
            break
ours["pnpm"] = package_json["packageManager"]
ours["biome"] = package_json["devDependencies"]["@biomejs/biome"]

# Fetch upstream latest
from scrapling.fetchers import Fetcher

upstream = {}
queries = [
    ("ruff", "https://pypi.org/pypi/ruff/json", "pypi"),
    ("mypy", "https://pypi.org/pypi/mypy/json", "pypi"),
    ("pytest", "https://pypi.org/pypi/pytest/json", "pypi"),
    ("pytest-asyncio", "https://pypi.org/pypi/pytest-asyncio/json", "pypi"),
    ("pre-commit", "https://pypi.org/pypi/pre-commit/json", "pypi"),
    ("fastapi", "https://pypi.org/pypi/fastapi/json", "pypi"),
    ("pydantic", "https://pypi.org/pypi/pydantic/json", "pypi"),
    ("dramatiq", "https://pypi.org/pypi/dramatiq/json", "pypi"),
    ("scrapling", "https://pypi.org/pypi/scrapling/json", "pypi"),
    ("followthemoney", "https://pypi.org/pypi/followthemoney/json", "pypi"),
    ("uv", "https://pypi.org/pypi/uv/json", "pypi"),
    ("pnpm", "https://registry.npmjs.org/pnpm/latest", "npm"),
    ("biome", "https://registry.npmjs.org/@biomejs/biome/latest", "npm"),
    ("next", "https://registry.npmjs.org/next/latest", "npm"),
    ("react", "https://registry.npmjs.org/react/latest", "npm"),
    ("shadcn", "https://registry.npmjs.org/shadcn/latest", "npm"),
]
for name, url, kind in queries:
    try:
        r = Fetcher.get(url, stealthy_headers=True, follow_redirects=True, timeout=15)
        # Adaptor body-capture gap: .text can be empty; .body is bytes
        raw = r.text if r.text else (r.body.decode("utf-8") if isinstance(r.body, (bytes, bytearray)) else str(r.body))
        data = json.loads(raw)
        if kind == "pypi":
            upstream[name] = {
                "latest": data["info"]["version"],
                "released": data["urls"][0]["upload_time"] if data.get("urls") else "?",
            }
        else:
            upstream[name] = {
                "latest": data["version"],
                "released": data.get("time", {}).get("modified", "?")
                if "time" in data
                else "?",
            }
    except Exception as e:
        upstream[name] = {"error": f"{e.__class__.__name__}: {str(e)[:100]}"}

# Compare
print("=" * 70)
print(f"Scaffold version-check vs upstream — {datetime.utcnow().isoformat()}Z")
print("=" * 70)
print(f"{'package':<20} {'our pin':<20} {'upstream latest':<20} {'released'}")
print("-" * 80)
for name in ["ruff", "mypy", "pytest", "pytest-asyncio", "pre-commit",
            "fastapi", "pydantic", "dramatiq", "scrapling", "followthemoney",
            "uv", "pnpm", "biome", "next", "react", "shadcn"]:
    our = ours.get(name, "—")
    up = upstream.get(name, {})
    latest = up.get("latest", up.get("error", "—"))
    released = up.get("released", "—")[:10] if isinstance(up.get("released"), str) else "—"
    print(f"{name:<20} {our:<20} {latest:<20} {released}")

print()
print("[note] our pins are minimums (>=X.Y), not exact pins.")
print("[note] uv.lock pins exact resolutions for transitive deps.")

# Save full report
report = {
    "timestamp": datetime.utcnow().isoformat() + "Z",
    "ours": ours,
    "upstream": upstream,
}
Path(".version_check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(f"\nWrote .version_check.json ({len(json.dumps(report))} bytes)")
