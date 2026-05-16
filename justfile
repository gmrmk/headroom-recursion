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

# Identity Triangulation sprint -- bootstrap IP intel reference databases
# (MaxMind GeoLite2, IP2Proxy LITE, Tor exit-list, X4BNet ranges) into
# data/reference/. Knowledge bases, not target data. Required env for
# the gated sources:
#   MAXMIND_LICENSE_KEY      free w/ MaxMind account
#   IP2PROXY_DOWNLOAD_TOKEN  free w/ ip2location.com account
# Public sources (tor, x4bnet) need no auth.
refdata *flags:
    uv run python infra/scripts/fetch_ip_refdata.py {{flags}}

# Priya R-10 phase6: four-service health probe; target <10s wall, all green
# means the M0 dev stack is up. Detail per service printed when red.
smoke:
    uv run python tools/dev/smoke.py
