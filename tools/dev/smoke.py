"""Four-service smoke test for the M0 dev stack (Priya phase6 P1).

Probes the four services that `start-dev.ps1` launches and prints a
one-screen pass/fail table. Target: complete in <10 seconds on a warm
stack. Concurrent probes via asyncio.gather so the wall time is
dominated by the slowest single service.

Services probed:

    FastAPI    http://127.0.0.1:8000/healthz  -> JSON {"status":"ok"}
    Next.js    http://127.0.0.1:3000/         -> HTTP 200
    Redis      127.0.0.1:6379                 -> raw socket PING reply
    Dramatiq   {Get-Process}                  -> at least one python.exe
                                                 holding the dramatiq actor

Exit codes:
    0  all four green
    1  one or more red (printed in the table)
    2  invocation error (e.g. httpx not installed)

CLI:
    python tools/dev/smoke.py                          # all four
    python tools/dev/smoke.py --service api            # one service
    python tools/dev/smoke.py --timeout 2.5            # custom per-probe timeout
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import socket
import subprocess
import sys
import time
from dataclasses import dataclass

DEFAULT_TIMEOUT_S = 2.0


@dataclass(slots=True)
class ProbeResult:
    service: str
    ok: bool
    detail: str
    elapsed_ms: int


async def _probe_http(
    name: str, url: str, expect_json_status_ok: bool, timeout_s: float
) -> ProbeResult:
    """HTTP GET with optional JSON shape check."""
    t0 = time.perf_counter()
    try:
        import httpx
    except ImportError:
        return ProbeResult(name, False, "httpx not installed (uv sync --all-packages)", 0)
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return ProbeResult(
                name, False, f"HTTP {r.status_code}", int((time.perf_counter() - t0) * 1000)
            )
        if expect_json_status_ok:
            try:
                payload = r.json()
            except ValueError:
                return ProbeResult(
                    name,
                    False,
                    "200 OK but body is not JSON",
                    int((time.perf_counter() - t0) * 1000),
                )
            if payload.get("status") != "ok":
                return ProbeResult(
                    name,
                    False,
                    f"200 OK but status={payload.get('status')!r}",
                    int((time.perf_counter() - t0) * 1000),
                )
        return ProbeResult(name, True, "OK", int((time.perf_counter() - t0) * 1000))
    except httpx.ConnectError:
        return ProbeResult(
            name, False, f"connection refused ({url})", int((time.perf_counter() - t0) * 1000)
        )
    except httpx.TimeoutException:
        return ProbeResult(
            name, False, f"timeout after {timeout_s}s", int((time.perf_counter() - t0) * 1000)
        )
    except Exception as exc:
        return ProbeResult(
            name, False, f"{type(exc).__name__}: {exc}", int((time.perf_counter() - t0) * 1000)
        )


async def _probe_redis(timeout_s: float) -> ProbeResult:
    """Raw socket PING/+PONG. Works against Memurai, WSL2 Redis, Dockered Redis."""
    t0 = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 6379), timeout=timeout_s
        )
        writer.write(b"PING\r\n")
        await writer.drain()
        reply = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        if reply.strip() in (b"+PONG", b"+PONG\r"):
            return ProbeResult("Redis", True, "PONG", int((time.perf_counter() - t0) * 1000))
        return ProbeResult(
            "Redis", False, f"unexpected reply: {reply!r}", int((time.perf_counter() - t0) * 1000)
        )
    except (TimeoutError, ConnectionRefusedError, OSError, socket.gaierror) as exc:
        return ProbeResult(
            "Redis", False, f"{type(exc).__name__}: {exc}", int((time.perf_counter() - t0) * 1000)
        )


async def _probe_dramatiq() -> ProbeResult:
    """Look for any python.exe process whose cmdline contains 'dramatiq'.

    Cheap heuristic; doesn't tell us the actor is healthy, only that the
    process is running. Real readiness is the API's ability to enqueue a
    task and observe an event (covered by integration tests, not smoke).
    """
    t0 = time.perf_counter()
    try:
        # PowerShell on Win11 -- Get-CimInstance is the supported way to get cmdline.
        # On POSIX we'd use `pgrep -af dramatiq`. Smoke test runs on dev machines
        # where Win11 is the default; POSIX shim included for completeness.
        if sys.platform == "win32":
            proc = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.CommandLine -like '*dramatiq*osint_goblin*' } | "
                    "Measure-Object | Select-Object -ExpandProperty Count",
                ],
                capture_output=True,
                text=True,
                timeout=4.0,
            )
            count = int((proc.stdout or "0").strip() or "0")
        else:
            proc = subprocess.run(
                ["pgrep", "-af", "dramatiq.*osint_goblin"],
                capture_output=True,
                text=True,
                timeout=4.0,
            )
            count = len([line for line in proc.stdout.splitlines() if line.strip()])
        if count >= 1:
            return ProbeResult(
                "Dramatiq",
                True,
                f"{count} worker process(es) running",
                int((time.perf_counter() - t0) * 1000),
            )
        return ProbeResult(
            "Dramatiq", False, "no worker process found", int((time.perf_counter() - t0) * 1000)
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(
            "Dramatiq", False, "process scan timed out", int((time.perf_counter() - t0) * 1000)
        )
    except Exception as exc:
        return ProbeResult(
            "Dramatiq",
            False,
            f"{type(exc).__name__}: {exc}",
            int((time.perf_counter() - t0) * 1000),
        )


async def _run_probes(services: list[str], timeout_s: float) -> list[ProbeResult]:
    coros: list[asyncio.Future[ProbeResult] | asyncio.Task[ProbeResult]] = []
    if "api" in services:
        coros.append(
            asyncio.create_task(
                _probe_http("FastAPI", "http://127.0.0.1:8000/healthz", True, timeout_s)
            )
        )
    if "web" in services:
        coros.append(
            asyncio.create_task(_probe_http("Next.js", "http://127.0.0.1:3000/", False, timeout_s))
        )
    if "redis" in services:
        coros.append(asyncio.create_task(_probe_redis(timeout_s)))
    if "dramatiq" in services:
        coros.append(asyncio.create_task(_probe_dramatiq()))
    return list(await asyncio.gather(*coros))


def _print_table(results: list[ProbeResult]) -> None:
    print()
    print(f"  {'Service':<11}  {'Status':<7}  {'Time':>7}  Detail")
    print(f"  {'-' * 11}  {'-' * 7}  {'-' * 7}  {'-' * 40}")
    for r in results:
        marker = "PASS" if r.ok else "FAIL"
        print(f"  {r.service:<11}  {marker:<7}  {r.elapsed_ms:>5} ms  {r.detail}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OSINT GOBLIN dev-stack smoke test (Priya phase6 R-10)."
    )
    parser.add_argument(
        "--service",
        choices=["api", "web", "redis", "dramatiq"],
        action="append",
        help="Probe only the named service; repeat for multiple. Default: all four.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"Per-probe timeout in seconds (default: {DEFAULT_TIMEOUT_S}).",
    )
    args = parser.parse_args()
    services = args.service or ["api", "web", "redis", "dramatiq"]

    t0 = time.perf_counter()
    results = asyncio.run(_run_probes(services, args.timeout))
    wall_s = time.perf_counter() - t0

    _print_table(results)
    failed = [r.service for r in results if not r.ok]
    if failed:
        print(
            f"  RESULT: {len(failed)} service(s) red: {', '.join(failed)}  (wall {wall_s:.2f}s)\n"
        )
        return 1
    print(f"  RESULT: all {len(results)} service(s) green  (wall {wall_s:.2f}s)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
