# OSINT GOBLIN — Upgrade Path

Captured 2026-05-11 via Scrapling-driven verification (`.scrapling_version_check.py`).
The scaffold pins target-stability versions; the current upstream latest is documented
below with an upgrade trigger and recommended Sprint.

## Major-version deltas open as of 2026-05-11

| Package        | Pinned (scaffold) | Upstream latest    | Status                                                                                     | Upgrade trigger                                                                                            | Owner          |
|----------------|-------------------|--------------------|--------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|----------------|
| **Next.js**    | 15.x              | 16.2.6             | M1 holds at 15 (INTEGRATION-SPEC §11 reference)                                            | Sprint 7+ once Mei-Lan validates App Router 16 SSE behavior. Specifically check `force-dynamic` + `X-Accel-Buffering: no` flush. | Mei-Lan        |
| **pnpm**       | 9.12.0            | 11.0.9             | M1 holds at 9 (workspace tooling stable)                                                   | Sprint 8+ after pnpm 11 changeset / workspace migration notes are read in full.                            | Priya          |
| **Biome**      | 1.9.4             | 2.4.15             | M1 holds at 1.9                                                                            | Sprint 7+ when biome.json v2 schema is validated against our rule set.                                     | Priya          |
| **mypy**       | `>=1.13,<2`       | 2.0.0              | M1 pins band excluding 2.0 (strict-mode behaviors changed)                                 | Sprint 9+ after strict-mode regression test fixture is written.                                            | Yuki / Diego   |
| **pytest**     | `>=8,<10`         | 9.0.3              | M1 allows 9 (compatible), excludes future 10                                               | Sprint 10+ — pytest 10 not yet released, but pin keeps room.                                              | Yuki           |
| **Scrapling**  | 0.4.7 (empirical venv); scaffold tracks `>=0.4` | 0.4.8 | Patch upgrade trivially adoptable                                                          | Bump in next `uv lock` refresh; no action needed.                                                          | Diego          |

## Untracked (no action needed)

- `fastapi` / `pydantic` / `dramatiq` — unpinned in dev-deps; per-app pyprojects pick stable.
- `react` 19.2.6 — already on the 19 line.

## How to revalidate

```powershell
cd <repo-root>
& "C:/Users/strid/osint-dashboard-research/empirical/.venv/Scripts/python.exe" `
  .scrapling_version_check.py
```

Compares pyproject.toml + package.json against PyPI + npm. Output also lands in `.version_check.json`.

## Doctrine

The scaffold deliberately pins to the versions that the Phase-3 design persona team had on the table when they decided. **Upgrading a major during M0/M1 = re-decision, not maintenance.** Sprint 7+ is when the dust settles and Mei-Lan/Priya/Diego/Yuki can opine on each individually with the test pyramid green.
