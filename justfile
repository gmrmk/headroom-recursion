# OSINT GOBLIN — task runner (cross-platform)
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

default:
    @just --list

install:
    uv sync
    pnpm install

lint:
    uv run ruff check .
    uv run python tools/ci/agpl_import_lint.py
    uv run python tools/ci/module_dag_lint.py

test:
    uv run pytest -x

agpl-lint:
    uv run python tools/ci/agpl_import_lint.py

# Priya R-10 phase6: four-service health probe; target <10s wall, all green
# means the M0 dev stack is up. Detail per service printed when red.
smoke:
    uv run python tools/dev/smoke.py
