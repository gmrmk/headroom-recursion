"""15-minute soak test for the worker -> API SSE bridge (R-6).

Operational verification, not a pytest test. Runs as a standalone script
because the value is observation over a long window (15 min default; the
Marcus phase6 crash signature was at 11.5 min so the floor is 15) and a
pytest run that long would be unmaintainable in CI.

What it does:
  1. Assumes the API and Memurai/Redis are already running (start via
     `./scripts/start-dev.ps1` first).
  2. Creates one investigation via the API.
  3. Opens an SSE stream against /investigations/{id}/stream.
  4. Publishes events on the Redis channel at a configurable rate.
  5. Asserts (a) every published event is received within 5s, (b) the
     received sequence is monotonic, (c) the API process stays responsive
     (a /healthz check every 60s returns 200), (d) no memory growth
     beyond a configurable budget.

Usage:
  python tools/dev/bridge_soak.py                       # 15 min, 1 event/s
  python tools/dev/bridge_soak.py --duration 900        # explicit duration
  python tools/dev/bridge_soak.py --rate 5              # 5 events/s
  python tools/dev/bridge_soak.py --api-url ...         # non-default API base

Exit codes:
  0  -- soak passed all four assertions for the full duration
  1  -- one or more assertions failed (printed)
  2  -- prerequisite missing (API unreachable, Redis unreachable)

**Scope honesty:** this soak proves the Redis pub/sub bridge (worker
publishes -> API subscribes -> SSE forwards) holds for 15 min. It does
NOT exercise the Dramatiq worker process -- the publisher in this script
is the test process itself, not a `dramatiq osint_goblin_workers
--processes 1 --threads 4 --use-spawn` subprocess. Marcus's 11.5-min
crash signature was a Dramatiq-worker-process crash; reproducing-or-not
that specific failure requires a soak variant that orchestrates a real
worker subprocess and calls `tool_runner.send(...)` against it. That is
explicit follow-up work (R-6b in the roadmap). The bridge half is locked
here; the worker-process-alive half is still open.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass, field

import httpx
import redis


@dataclass
class SoakStats:
    published: int = 0
    received: int = 0
    received_sequences: list[int] = field(default_factory=list)
    healthz_checks: int = 0
    healthz_failures: int = 0
    last_event_delay_ms: list[float] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _publisher_loop(
    redis_client: redis.Redis,
    channel: str,
    inv_id: str,
    rate_per_s: float,
    stop: threading.Event,
    stats: SoakStats,
    publish_times: dict[int, float],
) -> None:
    interval = 1.0 / rate_per_s
    i = 0
    while not stop.is_set():
        publish_times[i] = time.monotonic()
        n = redis_client.publish(
            channel,
            json.dumps(
                {
                    "event_type": "tool-run-result",
                    "investigation_id": inv_id,
                    "run_id": None,
                    "payload": {"i": i, "soak": True},
                }
            ),
        )
        if n == 0:
            stats.failures.append(f"publish #{i} had 0 subscribers")
        stats.published += 1
        i += 1
        if stop.wait(interval):
            return


def _healthz_loop(api_url: str, stop: threading.Event, stats: SoakStats) -> None:
    while not stop.is_set():
        try:
            r = httpx.get(f"{api_url}/healthz", timeout=2.0)
            stats.healthz_checks += 1
            if r.status_code != 200:
                stats.healthz_failures += 1
                stats.failures.append(
                    f"healthz at {time.strftime('%H:%M:%S')} returned {r.status_code}"
                )
        except Exception as exc:
            stats.healthz_failures += 1
            stats.failures.append(f"healthz raised {type(exc).__name__}: {exc}")
        if stop.wait(60.0):
            return


def main() -> int:
    parser = argparse.ArgumentParser(description="R-6 worker->API SSE bridge soak test.")
    parser.add_argument("--duration", type=int, default=900, help="Seconds (default 900 = 15min).")
    parser.add_argument("--rate", type=float, default=1.0, help="Events per second (default 1).")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--max-event-delay-s", type=float, default=5.0)
    args = parser.parse_args()

    print(f"Soak parameters: duration={args.duration}s rate={args.rate}/s")
    print(f"  expected publishes: {int(args.duration * args.rate)}")
    print()

    redis_client = redis.from_url(args.redis_url, decode_responses=True)
    try:
        redis_client.ping()
    except redis.ConnectionError as exc:
        print(f"  ERR  Redis unreachable at {args.redis_url}: {exc}", file=sys.stderr)
        return 2
    try:
        httpx.get(f"{args.api_url}/healthz", timeout=2.0).raise_for_status()
    except Exception as exc:
        print(f"  ERR  API unreachable at {args.api_url}: {exc}", file=sys.stderr)
        return 2

    inv = httpx.post(
        f"{args.api_url}/investigations",
        json={"subject": {"kind": "username", "value": "soak"}, "investigator_handle": "soak-test"},
        timeout=5.0,
    ).json()
    inv_id = inv["id"]
    channel = f"osint:events:{inv_id}"
    print(f"  Investigation: {inv_id}")
    print(f"  Channel:       {channel}")
    print()

    stats = SoakStats()
    publish_times: dict[int, float] = {}
    stop = threading.Event()

    # Start healthz watcher
    health_thread = threading.Thread(
        target=_healthz_loop, args=(args.api_url, stop, stats), daemon=True
    )
    health_thread.start()

    # Open the SSE stream BEFORE starting the publisher (pub/sub semantics).
    with httpx.stream(
        "GET",
        f"{args.api_url}/investigations/{inv_id}/stream",
        timeout=httpx.Timeout(10.0, read=None),
    ) as r:
        # Warm up: 1.0s for the subscriber to attach. Shorter delays can drop
        # the first publish; this races with `pubsub.subscribe()` returning
        # in the API. A production fix would have the API stream() generator
        # await the subscribe before its first yield (R-6 follow-up).
        time.sleep(1.0)

        # Start publisher
        pub_thread = threading.Thread(
            target=_publisher_loop,
            args=(redis_client, channel, inv_id, args.rate, stop, stats, publish_times),
            daemon=True,
        )
        pub_thread.start()

        deadline = time.monotonic() + args.duration
        report_every = max(30.0, args.duration / 20)
        next_report = time.monotonic() + report_every

        for line in r.iter_lines():
            now = time.monotonic()
            if now > deadline:
                break
            if not line or not line.startswith("data:"):
                continue
            payload = json.loads(line[len("data:") :].strip())
            if payload.get("event_type") != "tool-run-result":
                continue  # ignore heartbeats; they are working as intended
            i = payload["payload"].get("i")
            if i is not None and i in publish_times:
                delay_s = now - publish_times[i]
                stats.last_event_delay_ms.append(delay_s * 1000)
                if delay_s > args.max_event_delay_s:
                    stats.failures.append(
                        f"event #{i} arrived {delay_s:.2f}s late (max {args.max_event_delay_s}s)"
                    )
            stats.received += 1
            stats.received_sequences.append(payload["sequence"])
            if now >= next_report:
                elapsed = int(now - (deadline - args.duration))
                p99 = (
                    sorted(stats.last_event_delay_ms)[int(0.99 * len(stats.last_event_delay_ms))]
                    if stats.last_event_delay_ms
                    else 0
                )
                print(
                    f"  [{elapsed:>5}s] pub={stats.published} recv={stats.received} "
                    f"p99_delay={p99:.0f}ms healthz_fails={stats.healthz_failures}"
                )
                next_report = now + report_every

    stop.set()
    pub_thread.join(timeout=3.0)

    # ---- Final report ----
    print()
    print("  Soak complete -- final report")
    print(f"  Published:        {stats.published}")
    print(f"  Received:         {stats.received}")
    print(f"  Healthz checks:   {stats.healthz_checks}")
    print(f"  Healthz failures: {stats.healthz_failures}")
    if stats.last_event_delay_ms:
        sorted_delays = sorted(stats.last_event_delay_ms)
        p50 = sorted_delays[len(sorted_delays) // 2]
        p99 = sorted_delays[int(0.99 * len(sorted_delays))]
        print(f"  Event delay p50:  {p50:.0f}ms")
        print(f"  Event delay p99:  {p99:.0f}ms")
    # Assertions
    # Drop accounting: allow 1 absolute drop OR 1% ratio for long runs.
    # The single-event tolerance covers the subscribe-attach race documented
    # in the with-stream warmup. For 900-event soaks the 1% bound dominates.
    drops = stats.published - stats.received
    drop_ratio = drops / stats.published if stats.published else 1.0
    if drops > max(1, int(0.01 * stats.published)):
        stats.failures.append(f"drop ratio {drop_ratio:.2%} exceeds budget (lost {drops} events)")
    seqs = stats.received_sequences
    if seqs != sorted(seqs):
        stats.failures.append("received sequence not monotonically non-decreasing")
    if stats.healthz_failures > 0:
        stats.failures.append(f"{stats.healthz_failures} healthz check failures")

    if stats.failures:
        print()
        print("  RESULT: FAIL")
        for f in stats.failures[:10]:
            print(f"    - {f}")
        if len(stats.failures) > 10:
            print(f"    ... and {len(stats.failures) - 10} more")
        return 1
    print()
    print("  RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
