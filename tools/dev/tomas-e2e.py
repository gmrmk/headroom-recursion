"""Tomás end-to-end investigation runner.

Dispatches every workflow against The Octocat (deliberately public test
entity: GitHub's mascot + public infra). Streams SSE for ~60s, prints a
per-workflow summary of every event that arrived.

This is the smoke test the Playwright suite *should* be passing. If this
script reports zero events for any workflow, the chain is broken there.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import defaultdict

import httpx

API = "http://127.0.0.1:8000"

SUBJECT = {
    "address": "88 Colin P Kelly Jr St, San Francisco, CA",
    "host": "The Octocat",
    "photo": "https://github.com/octocat.png",
    "email": "octocat@github.com",
    "username": "octocat",
    "ip": "140.82.114.4",
    "domain": "github.com",
}


def create_investigation() -> str:
    r = httpx.post(
        f"{API}/investigations",
        json={
            "subject": {"kind": "person", "value": SUBJECT["host"]},
            "investigator_handle": "tomas-e2e",
            "notes": "",
        },
        timeout=5,
    )
    r.raise_for_status()
    return r.json()["id"]


def dispatch(inv_id: str, adapter_id: str, payload: dict) -> dict:
    r = httpx.post(
        f"{API}/investigations/{inv_id}/run",
        json={"adapter_id": adapter_id, "adapter_payload": payload},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def stream_events(inv_id: str, events_by_run: dict, stop_evt: threading.Event):
    """Consume the SSE stream. SSE format: lines `data: {json}` separated by blank lines."""
    url = f"{API}/investigations/{inv_id}/stream"
    try:
        with httpx.stream("GET", url, timeout=httpx.Timeout(5.0, read=90.0)) as r:
            r.raise_for_status()
            buf = ""
            for raw in r.iter_lines():
                if stop_evt.is_set():
                    return
                if raw is None:
                    continue
                if raw.startswith("data: "):
                    buf = raw[len("data: ") :]
                elif raw == "":
                    if buf:
                        try:
                            ev = json.loads(buf)
                            run_id = ev.get("run_id", "?")
                            events_by_run[run_id].append(ev)
                            print(
                                f"  [event] run={run_id[:8]} type={ev.get('event_type','?')} "
                                f"adapter={ev.get('adapter_id','?')}",
                                flush=True,
                            )
                        except Exception as e:
                            print(f"  [parse-fail] {e}: {buf[:120]}", flush=True)
                        buf = ""
    except Exception as e:
        print(f"[STREAM] error: {type(e).__name__}: {e}", flush=True)


def main() -> int:
    print("=" * 72)
    print("TOMÁS END-TO-END INVESTIGATION — The Octocat")
    print("=" * 72)

    inv_id = create_investigation()
    print(f"[create] investigation_id={inv_id}\n")

    events_by_run: dict[str, list] = defaultdict(list)
    stop_evt = threading.Event()
    t = threading.Thread(target=stream_events, args=(inv_id, events_by_run, stop_evt), daemon=True)
    t.start()
    time.sleep(0.5)  # let stream attach

    # Dispatch each workflow. adapter_id = workflow_id for workflows.
    plan = [
        ("w10.ip", {"ip": SUBJECT["ip"]}),
        ("w5.do", {"domain": SUBJECT["domain"]}),
        ("w1.un", {"username": SUBJECT["username"]}),
        ("w11.em", {"email": SUBJECT["email"]}),
        (
            "w9.pv",
            {
                "address": SUBJECT["address"],
                "host_name": SUBJECT["host"],
                "listing_photo_url": SUBJECT["photo"],
            },
        ),
    ]

    run_ids: dict[str, str] = {}
    for adapter_id, payload in plan:
        try:
            resp = dispatch(inv_id, adapter_id, payload)
            run_ids[resp["run_id"]] = adapter_id
            print(f"[dispatch] {adapter_id:8s} run_id={resp['run_id']}")
        except Exception as e:
            print(f"[dispatch] {adapter_id:8s} FAILED: {type(e).__name__}: {e}")
        time.sleep(0.1)

    print()
    print("[wait] streaming events for 60s ...")
    time.sleep(60)
    stop_evt.set()
    time.sleep(0.5)

    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)

    total = 0
    for run_id, adapter_id in run_ids.items():
        evs = events_by_run.get(run_id, [])
        total += len(evs)
        types = defaultdict(int)
        for ev in evs:
            types[ev.get("event_type", "?")] += 1
        status = "✓" if evs else "✗ NO EVENTS"
        print(
            f"  {status} {adapter_id:8s} run={run_id[:8]} events={len(evs):3d} types={dict(types)}"
        )

    print()
    print(f"TOTAL EVENTS: {total} across {len(run_ids)} workflows")

    if total == 0:
        print()
        print("[VERDICT] CHAIN IS BROKEN — no events arrived from any workflow.")
        print("Check: worker process consuming, broker URL match, SSE bridge wiring.")
        return 2
    print()
    print("[VERDICT] CHAIN WORKS — see counts above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
