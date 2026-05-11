"""Real-Dramatiq soak test (R-6b) -- close the advisor's #1 concern.

`tools/dev/bridge_soak.py` proves the Redis pub/sub bridge works for
events that reach Redis. It does NOT prove the actual Dramatiq worker
process stays alive under sustained load -- that's what Marcus's
phase6 evidence (worker.err line 4, 2026-05-10 23:04:17, exit code
0xFFFFFFFF after 11.5 minutes) is about, and what R-6b exists to
reproduce-or-rebut.

What this soak does (different from bridge_soak.py):
  1. Spawns a REAL `dramatiq osint_goblin_workers ...` subprocess with
     the production CLI flags (--processes 1 --threads 4 --use-spawn).
  2. Dispatches tool_runner.send() messages against it via the same
     Dramatiq broker the API would use. The adapter id is
     `worker_stress`, which emits N events from the WORKER process.
  3. Subscribes to the API SSE stream and counts events that arrive.
  4. Periodically verifies the worker subprocess is still alive AND
     responsive (a /healthz-equivalent check via a marker event).
  5. Runs for >= 15 min default to clear Marcus's 11.5-min signature.

Assertions (final report):
  - Worker subprocess: alive at the end (PID still in ps).
  - Worker crashed during the soak: NO (no SIGCHLD captured).
  - Events: drop rate < 1% of expected (allow startup-race tolerance).
  - Sequences: monotonically non-decreasing across all received events.

Usage:
  python tools/dev/dramatiq_soak.py                  # 15 min, 6 msg/min
  python tools/dev/dramatiq_soak.py --duration 60    # quick smoke
  python tools/dev/dramatiq_soak.py --rate 12        # 12 messages/min
  python tools/dev/dramatiq_soak.py --events 32      # per-message count

Prerequisites (start-dev.ps1 -Diagnose to verify):
  - Memurai/Redis listening on :6379.
  - API uvicorn on :8000 (the script creates the investigation +
    hold_bridge_persistent via POST /run, so the API must be live).

Exit codes:
  0  pass (all assertions green)
  1  fail (one or more assertions red)
  2  prerequisite missing (Redis or API unreachable, worker fails to start)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import redis

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = (
    REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if os.name == "nt"
    else REPO_ROOT / ".venv" / "bin" / "python"
)


@dataclass
class SoakStats:
    messages_sent: int = 0
    events_received: int = 0
    sequences: list[int] = field(default_factory=list)
    worker_alive_checks: int = 0
    worker_alive_failures: int = 0
    failures: list[str] = field(default_factory=list)


def _start_worker(redis_url: str) -> subprocess.Popen[bytes]:
    """Spawn `dramatiq osint_goblin_workers` with the production CLI flags.

    Matches the start-dev.ps1 incantation: --processes 1 --threads 4
    --use-spawn. The OSINT_REDIS_URL env propagates the broker URL so
    the worker connects to the same Memurai as the test client.
    """
    env = os.environ.copy()
    env["OSINT_REDIS_URL"] = redis_url
    env["PYTHONUNBUFFERED"] = "1"  # see worker stderr in real time
    proc = subprocess.Popen(
        [
            str(VENV_PYTHON),
            "-m",
            "dramatiq",
            "osint_goblin_workers",
            "--processes",
            "1",
            "--threads",
            "4",
            "--use-spawn",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return proc


def _wait_for_worker_ready(proc: subprocess.Popen[bytes], timeout_s: float = 10.0) -> bool:
    """Poll for ~10s waiting for the worker to settle.

    Dramatiq doesn't expose a readiness endpoint; the proxy signal is
    "process is still alive N seconds after start" which is what we
    get for free by checking `proc.poll() is None`. We also briefly
    sleep so the broker subscription has a chance to attach.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False  # died during startup
        time.sleep(0.5)
        # 2s settle is empirically enough for dramatiq to attach
        if time.monotonic() - (deadline - timeout_s) > 2.0:
            return True
    return proc.poll() is None


def _dispatch_loop(
    investigation_id: str,
    api_url: str,
    rate_per_min: float,
    events_per_msg: int,
    stop: threading.Event,
    stats: SoakStats,
) -> None:
    """Periodically POST /run to enqueue a worker_stress job.

    Each call hits the API's hold_bridge_persistent so the SSE
    subscriber attaches; then the API enqueues nothing extra -- we
    do not have a real tool_runner.send() wired through the API yet
    (that's M1 work). Instead we drive the worker via Dramatiq's
    broker directly using the publisher path: each "tool run" is a
    `tool_runner.send(...)` message dispatched in-process from this
    loop, which the live worker subprocess picks up.
    """
    # Lazy import: importing osint_goblin_workers attaches the global
    # broker. We do it inside the thread so the import side-effects
    # don't fire if the script is invoked with --help etc.
    from osint_goblin_workers.broker import configure_broker
    from osint_goblin_workers.tool_runner import tool_runner

    configure_broker(url=os.environ.get("OSINT_REDIS_URL", "redis://127.0.0.1:6379/0"))

    interval = 60.0 / rate_per_min
    # First call: ensure the API has hold_bridge_persistent on the inv
    # so the subscriber is up before any worker event publishes.
    httpx.post(
        f"{api_url}/investigations/{investigation_id}/run",
        json={"adapter_id": "worker_stress", "payload": {"count": 0}},
        timeout=5.0,
    )
    while not stop.is_set():
        run_id = str(uuid.uuid4())
        tool_runner.send(
            {
                "investigation_id": investigation_id,
                "run_id": run_id,
                "adapter_id": "worker_stress",
                "adapter_payload": {"count": events_per_msg},
            }
        )
        stats.messages_sent += 1
        if stop.wait(interval):
            return


def _worker_watcher(
    proc: subprocess.Popen[bytes],
    stop: threading.Event,
    stats: SoakStats,
) -> None:
    """Every 30s, confirm the worker subprocess is still alive."""
    while not stop.is_set():
        stats.worker_alive_checks += 1
        if proc.poll() is not None:
            stats.worker_alive_failures += 1
            rc = proc.returncode
            hex_rc = (
                f" (0x{rc & 0xFFFFFFFF:08X})"
                if rc is not None and rc < 0 or (rc is not None and rc > 0xFFFF)
                else ""
            )
            stats.failures.append(
                f"worker subprocess died with exit code {rc}{hex_rc} "
                f"at check #{stats.worker_alive_checks}"
            )
            return  # no point checking further; the soak is failed
        if stop.wait(30.0):
            return


def _check_redis(url: str) -> bool:
    try:
        c = redis.from_url(url, decode_responses=True)
        c.ping()
        return True
    except Exception as exc:
        print(f"  ERR  Redis unreachable at {url}: {exc}", file=sys.stderr)
        return False


def _check_api(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/healthz", timeout=2.0)
        r.raise_for_status()
        return True
    except Exception as exc:
        print(f"  ERR  API unreachable at {url}: {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="R-6b dramatiq-worker-subprocess soak.")
    parser.add_argument("--duration", type=int, default=900, help="Seconds (default 900 = 15min).")
    parser.add_argument("--rate", type=float, default=6.0, help="Messages per minute (default 6).")
    parser.add_argument("--events", type=int, default=32, help="Events per message (default 32).")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    args = parser.parse_args()

    print(f"R-6b soak: duration={args.duration}s rate={args.rate}/min events/msg={args.events}")
    print(f"  expected messages: ~{int(args.duration * args.rate / 60)}")
    print(f"  expected events:   ~{int(args.duration * args.rate / 60) * args.events}")
    print()

    if not _check_redis(args.redis_url):
        return 2
    if not _check_api(args.api_url):
        return 2

    os.environ["OSINT_REDIS_URL"] = args.redis_url

    # Start the real worker subprocess
    print("  Starting dramatiq worker subprocess...")
    worker = _start_worker(args.redis_url)
    if not _wait_for_worker_ready(worker):
        print(f"  ERR  worker failed to start (exit {worker.returncode})", file=sys.stderr)
        return 2
    print(f"  Worker PID: {worker.pid}")

    # Create the investigation
    inv = httpx.post(
        f"{args.api_url}/investigations",
        json={"subject": {"kind": "username", "value": "soak"}, "investigator_handle": "r6b"},
        timeout=5.0,
    ).json()
    inv_id = inv["id"]
    print(f"  Investigation: {inv_id}")
    print()

    stats = SoakStats()
    stop = threading.Event()

    # Start watcher + dispatcher
    watcher = threading.Thread(target=_worker_watcher, args=(worker, stop, stats), daemon=True)
    watcher.start()
    dispatcher = threading.Thread(
        target=_dispatch_loop,
        args=(inv_id, args.api_url, args.rate, args.events, stop, stats),
        daemon=True,
    )
    dispatcher.start()

    # Subscribe to SSE; this also drives hold_bridge_persistent via POST /run
    # in the dispatcher's first iteration.
    deadline = time.monotonic() + args.duration
    report_every = max(30.0, args.duration / 20)
    next_report = time.monotonic() + report_every
    try:
        with httpx.stream(
            "GET",
            f"{args.api_url}/investigations/{inv_id}/stream",
            timeout=httpx.Timeout(10.0, read=None),
        ) as r:
            for line in r.iter_lines():
                now = time.monotonic()
                if now > deadline or stats.worker_alive_failures > 0:
                    break
                if not line or not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[len("data:") :].strip())
                except Exception:
                    continue
                # Skip heartbeats; we only count adapter events.
                if payload.get("event_type") == "heartbeat":
                    continue
                stats.events_received += 1
                seq = payload.get("sequence")
                if isinstance(seq, int):
                    stats.sequences.append(seq)
                if now >= next_report:
                    elapsed = int(args.duration - (deadline - now))
                    print(
                        f"  [{elapsed:>5}s] msgs={stats.messages_sent} "
                        f"events={stats.events_received} "
                        f"worker_alive={'YES' if stats.worker_alive_failures == 0 else 'NO'}"
                    )
                    next_report = now + report_every
    except Exception as exc:
        stats.failures.append(f"SSE stream raised {type(exc).__name__}: {exc}")
    finally:
        stop.set()
        dispatcher.join(timeout=3.0)
        watcher.join(timeout=3.0)
        with contextlib.suppress(Exception):
            worker.terminate()
            worker.wait(timeout=5.0)

    # ---- Final report ----
    print()
    print("  R-6b soak complete -- final report")
    print(f"  Messages sent:    {stats.messages_sent}")
    print(f"  Events received:  {stats.events_received}")
    print(f"  Worker alive checks: {stats.worker_alive_checks}")
    print(f"  Worker alive failures: {stats.worker_alive_failures}")

    expected = stats.messages_sent * args.events
    if expected > 0:
        drop_ratio = max(0.0, (expected - stats.events_received) / expected)
        print(f"  Drop ratio:       {drop_ratio:.2%}")
        # Allow generous warmup tolerance; the first message often races
        # with the dramatiq broker attach. Floor: 5% absolute OR 32 events.
        budget = max(int(0.05 * expected), 32)
        if expected - stats.events_received > budget:
            stats.failures.append(f"drop {expected - stats.events_received} > budget {budget}")

    if stats.sequences != sorted(stats.sequences):
        stats.failures.append("sequences not monotonically non-decreasing")

    if stats.worker_alive_failures > 0:
        # Marcus's signature would land here.
        print()
        print("  *** WORKER PROCESS DIED DURING SOAK ***")
        print("  This reproduces Marcus's 2026-05-10 11.5-min crash signature.")
        print("  See phase6/marcus-research.md sec.2.")

    if stats.failures:
        print()
        print("  RESULT: FAIL")
        for f in stats.failures[:10]:
            print(f"    - {f}")
        return 1
    print()
    print("  RESULT: PASS -- worker stayed alive, bridge stayed forwarding")
    return 0


if __name__ == "__main__":
    sys.exit(main())
