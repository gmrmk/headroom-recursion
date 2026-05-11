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
