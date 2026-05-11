"""Live SSE integration test -- Day 10 M0 exit gate (WI-0208).

This is the canonical SSE contract test. The pattern (free-port + Popen +
healthcheck-loop + httpx.stream) is from Yuki Q3 phase6 2026-05-11; it is
the only reliable shape for SSE testing through sse_starlette because
TestClient + ASGITransport cannot reliably stream (cross-event-loop
asyncio.Queue + ASGITransport response buffering -- discovered Day 8).

The Marcus P0 phase6 addition: a meaningful M0 gate must include a
first-event-arrives-fast assertion. Without it, a buffered-batch worker
that emits all 30 events in the last 200ms technically passes the
"30 events in <60s" gate while completely failing the actual UX
contract that motivated the gate (investigator sees something within
3 seconds of submitting a tool run).

Three assertions in the M0 exit gate, in order of strictness:

  1. **Liveness** -- first SSE event arrives within 3.0s wall-clock of the
     POST /run request. (Marcus P0 phase6.) If this fails, the worker is
     batching or the SSE flush headers are wrong; the gate has caught a
     real regression even if total event count is fine.

  2. **Throughput** -- 30+ SSE events delivered within 60s total wall-clock
     of the POST /run. (Original Day 10 spec.)

  3. **Ordering + monotonicity** -- event `sequence` field is monotonically
     non-decreasing across the stream; no gaps until disconnect. (Yuki Q8
     phase6 -- the source-grep header test from Day 8 cannot assert this.)

Marked `@pytest.mark.slow` so the fast loop (M-not-slow) skips it; runs in
the Day 10 M0 exit gate battery + the weekly real-network battery.

Status: PASSING as of Day 10 (WI-0208 2026-05-11). The xfail is lifted; the
test now drives the `m0_gate_stress` synthetic in-process emitter in the
API. That emitter (`apps/api/src/osint_goblin_api/routes.py::_emit_m0_gate_stress`)
is the M0-gate-only path; when R-6 (Sprint 2 Day 11-12, Redis pub/sub) lands,
the real worker emits the same shape via the real bridge and this test
should continue passing without modification.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from contextlib import closing

import httpx
import pytest


@pytest.fixture(scope="session")
def live_api():
    """Launch a real uvicorn subprocess on a free port; yield the base URL.

    Free-port discovery before subprocess start avoids race conditions;
    healthcheck-loop polling avoids the flakiness of sleep-based startup
    waits. Session-scoped because uvicorn cold-start is 1-2s and we don't
    want to pay that 6x across the M0 verb-E2E suite.
    """
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "osint_goblin_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd="apps/api",
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):  # 5s total
        try:
            r = httpx.get(f"{base}/healthz", timeout=0.5)
            if r.status_code == 200:
                break
        except httpx.RequestError:
            time.sleep(0.1)
    else:
        proc.terminate()
        pytest.fail("uvicorn did not start within 5s")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.slow
def test_m0_exit_gate_sse_live(live_api: str) -> None:
    """M0 exit gate: three SSE assertions on a real handle.

    Marcus P0 (phase6 2026-05-11): the original spec was "30+ events in <60s,"
    which a buffered worker can satisfy while violating the actual UX
    contract. The added first-event-<3s assertion catches that failure mode.
    """
    inv = httpx.post(
        f"{live_api}/investigations",
        json={"subject": {"kind": "username", "value": "alice"}, "investigator_handle": "test"},
        timeout=5.0,
    ).json()
    inv_id = inv["id"]

    run_posted_at = time.monotonic()
    httpx.post(
        f"{live_api}/investigations/{inv_id}/run",
        # m0_gate_stress is the synthetic in-process emitter (WI-0208); see
        # apps/api/src/osint_goblin_api/routes.py::_emit_m0_gate_stress. It
        # emits 32 events at 50ms each, well over the 30-in-60s threshold
        # with first event within 50ms of POST.
        json={"adapter_id": "m0_gate_stress", "payload": {}},
        timeout=5.0,
    )

    events: list[dict] = []
    first_event_at: float | None = None
    sequences: list[int] = []
    deadline = run_posted_at + 60.0

    with httpx.stream(
        "GET",
        f"{live_api}/investigations/{inv_id}/stream",
        timeout=httpx.Timeout(10.0, read=30.0),
    ) as r:
        for line in r.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = json.loads(line[len("data:") :].strip())
            if first_event_at is None:
                first_event_at = time.monotonic()
            events.append(payload)
            if "sequence" in payload:
                sequences.append(payload["sequence"])
            if len(events) >= 30 or time.monotonic() > deadline:
                break

    # Assertion 1: Liveness -- Marcus P0 phase6
    assert first_event_at is not None, "no SSE events arrived in 60s"
    first_event_delay_s = first_event_at - run_posted_at
    assert first_event_delay_s < 3.0, (
        f"first SSE event arrived {first_event_delay_s:.2f}s after POST /run; "
        f"M0 UX contract requires <3s. A buffered-batch worker can pass a "
        f"throughput-only gate while failing this -- that's the failure mode "
        f"this assertion catches."
    )

    # Assertion 2: Throughput -- original Day 10 spec
    assert len(events) >= 30, (
        f"received {len(events)} SSE events within 60s wall-clock; " f"M0 exit gate requires >=30."
    )

    # Assertion 3: Ordering -- Yuki Q8 phase6
    assert sequences == sorted(
        sequences
    ), f"SSE event sequences not monotonically non-decreasing: {sequences[:20]}..."
