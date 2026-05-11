"""ghunt subprocess wrapper — AGPL-3.0 boundary.

Reads JSON on stdin, writes NDJSON on stdout. Stderr is captured separately
by the dashboard's tool_runner actor. This file is the ONLY place
`import ghunt` is permitted (see tools/ci/agpl_import_lint.py).
"""
# import ghunt  # noqa: ERA001 — would be the real import; scaffold-spike leaves it commented
