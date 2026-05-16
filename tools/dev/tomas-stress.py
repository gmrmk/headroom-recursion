"""Tomas stress harness.

Cycles through fabricated identities, dispatches every workflow against
real upstream sources, streams events, and records per-workflow outcomes.

A "break" is any of:
  - dispatch HTTP failure
  - workflow produces zero events
  - workflow produces only tool-run-error events (no real results)
  - parse failure on an event
  - SSE stream drops

On break, dumps full diagnostic and prints WHY. Then continues iterating
unless --stop-on-break is given.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

API = "http://127.0.0.1:8000"

# Deliberately fabricated identities -- names + emails + phones that do
# not match any real person (host pattern keywords + random handles).
# IPs and domains ARE real (public infra / well-known sites) -- those
# legitimately resolve at upstream sources.
IDENTITIES = [
    {
        "label": "sarah-sf",
        "address": "100 Embarcadero, San Francisco, CA",
        "host": "Sarah Mitchell",
        "photo": "https://github.com/octocat.png",
        "email": "sarah.mitchell.host@gmail.com",
        "phone": "+14155551234",
        "username": "sarahmitchell_sf",
        "ip": "9.9.9.9",
        "domain": "airbnb.com",
    },
    {
        "label": "marcus-nyc",
        "address": "405 Lexington Ave, New York, NY",
        "host": "Marcus Chen",
        "photo": "https://github.com/defunkt.png",
        "email": "marcus.chen.bnb@yahoo.com",
        "phone": "+12125558765",
        "username": "marcuschen_nyc",
        "ip": "1.1.1.1",
        "domain": "vrbo.com",
    },
    {
        "label": "emily-la",
        "address": "8949 Sunset Blvd, West Hollywood, CA",
        "host": "Emily Rodriguez",
        "photo": "https://github.com/gaearon.png",
        "email": "emily.r.host@outlook.com",
        "phone": "+13105552345",
        "username": "emilyr_la",
        "ip": "208.67.222.222",
        "domain": "github.com",
    },
    {
        "label": "james-chi",
        "address": "875 N Michigan Ave, Chicago, IL",
        "host": "James Kowalski",
        "photo": "https://github.com/mitchellh.png",
        "email": "james.kowalski.bnb@protonmail.com",
        "phone": "+17735556789",
        "username": "jameskowalski_chi",
        "ip": "8.8.4.4",
        "domain": "mit.edu",
    },
    {
        "label": "aisha-sea",
        "address": "400 Broad St, Seattle, WA",
        "host": "Aisha Patel",
        "photo": "https://github.com/sindresorhus.png",
        "email": "aisha.patel.stay@gmail.com",
        "phone": "+12065554321",
        "username": "aishapatel_sea",
        "ip": "4.2.2.2",
        "domain": "stanford.edu",
    },
]


@dataclass
class RunResult:
    adapter_id: str
    run_id: str
    events: list = field(default_factory=list)

    @property
    def event_types(self) -> dict:
        out: dict = defaultdict(int)
        for e in self.events:
            out[e.get("event_type", "?")] += 1
        return dict(out)

    @property
    def has_real_result(self) -> bool:
        """Did anything beyond errors/accepted/heartbeat come back?"""
        for e in self.events:
            t = e.get("event_type", "")
            if t in ("tool-run-result", "tool-run-emit", "finding"):
                return True
        return False

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.events if e.get("event_type") == "tool-run-error")


@dataclass
class IterationResult:
    identity_label: str
    investigation_id: str
    runs: dict[str, RunResult] = field(default_factory=dict)  # run_id -> RunResult
    all_events: list = field(default_factory=list)  # every event (incl. child runs)
    breaks: list[str] = field(default_factory=list)
    real_errors: list[str] = field(default_factory=list)  # adapter-level failures

    @property
    def total_events(self) -> int:
        return len(self.all_events)

    @property
    def finding_counts(self) -> dict:
        """Count events by type for actual findings (non-noise)."""
        out: dict = defaultdict(int)
        noise = {
            "tool-run-accepted",
            "tool-run-result",
            "heartbeat",
            "PARSE_FAIL",
            "STREAM_DROPPED",
            "STREAM_ERROR",
        }
        for ev in self.all_events:
            t = ev.get("event_type", "?")
            if t in noise:
                continue
            out[t] += 1
        return dict(out)


def create_investigation(host: str) -> str:
    r = httpx.post(
        f"{API}/investigations",
        json={
            "subject": {"kind": "person", "value": host},
            "investigator_handle": "tomas-stress",
            "notes": "",
        },
        timeout=5,
    )
    r.raise_for_status()
    return r.json()["id"]


def dispatch(inv_id: str, adapter_id: str, payload: dict) -> dict:
    r = httpx.post(
        f"{API}/investigations/{inv_id}/run",
        json={"adapter_id": adapter_id, "payload": payload},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def stream_events(
    inv_id: str, runs: dict[str, RunResult], stop_evt: threading.Event, on_event=None
) -> None:
    url = f"{API}/investigations/{inv_id}/stream"
    try:
        with httpx.stream("GET", url, timeout=httpx.Timeout(5.0, read=120.0)) as r:
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
                    if not buf:
                        continue
                    try:
                        ev = json.loads(buf)
                    except json.JSONDecodeError as e:
                        if on_event:
                            on_event({"event_type": "PARSE_FAIL", "raw": buf[:200], "err": str(e)})
                        buf = ""
                        continue
                    finally:
                        # Always clear -- one event per data: line in our stream
                        pass
                    run_id = ev.get("run_id")
                    if run_id and run_id in runs:
                        runs[run_id].events.append(ev)
                    if on_event:
                        on_event(ev)
                    buf = ""
    except httpx.RemoteProtocolError as e:
        if on_event:
            on_event({"event_type": "STREAM_DROPPED", "err": str(e)})
    except Exception as e:
        if on_event:
            on_event({"event_type": "STREAM_ERROR", "err": f"{type(e).__name__}: {e}"})


def run_iteration(identity: dict, wait_seconds: int = 45) -> IterationResult:
    label = identity["label"]
    print(f"\n[{label}] " + "-" * 60)
    inv_id = create_investigation(identity["host"])
    print(f"[{label}] investigation_id={inv_id}")

    result = IterationResult(identity_label=label, investigation_id=inv_id)

    def on_event(ev: dict) -> None:
        t = ev.get("event_type", "?")
        rid = (ev.get("run_id") or "")[:8]
        result.all_events.append(ev)
        if t == "PARSE_FAIL":
            result.breaks.append(f"PARSE_FAIL: {ev.get('err')}")
            print(f"  [{label}] [BREAK] parse-fail: {ev.get('err')}")
        elif t == "STREAM_DROPPED":
            result.breaks.append(f"STREAM_DROPPED: {ev.get('err')}")
            print(f"  [{label}] [BREAK] stream dropped: {ev.get('err')}")
        elif t == "STREAM_ERROR":
            result.breaks.append(f"STREAM_ERROR: {ev.get('err')}")
            print(f"  [{label}] [BREAK] stream error: {ev.get('err')}")
        elif t == "heartbeat":
            return  # silent
        elif t == "tool-run-error":
            payload = ev.get("payload") or {}
            adapter = payload.get("adapter_id") or ev.get("adapter_id") or "?"
            reason = payload.get("reason") or payload.get("error") or payload.get("message") or "?"
            kind = payload.get("kind") or "?"
            # Only record adapter-level errors (not workflow skip events,
            # which are noise from missing optional seed keys).
            if "workflow step" not in str(reason):
                result.real_errors.append(f"{adapter}: {str(reason)[:200]}")
            print(
                f"  [{label}] ERR  run={rid} adapter={adapter:30s} kind={kind:14s} :: {str(reason)[:140]}"
            )
        elif t == "tool-run-result":
            payload = ev.get("payload") or {}
            adapter = payload.get("adapter_id") or ev.get("adapter_id") or "?"
            findings = (
                payload.get("findings") or payload.get("matches") or payload.get("results") or []
            )
            n_findings = len(findings) if isinstance(findings, list) else "?"
            print(f"  [{label}] RES  run={rid} adapter={adapter:30s} findings={n_findings}")
        else:
            adapter = (ev.get("payload") or {}).get("adapter_id") or ev.get("adapter_id") or "?"
            print(f"  [{label}] event run={rid} {t} adapter={adapter}")

    stop_evt = threading.Event()
    t = threading.Thread(
        target=stream_events,
        args=(inv_id, result.runs, stop_evt),
        kwargs={"on_event": on_event},
        daemon=True,
    )
    t.start()
    time.sleep(0.5)

    plan = [
        ("w10.ip", {"ip": identity["ip"]}),
        ("w5.do", {"domain": identity["domain"]}),
        ("w1.un", {"username": identity["username"]}),
        ("w11.em", {"email": identity["email"]}),
        ("w3.ph", {"phone": identity["phone"]}),
        (
            "w9.pv",
            {
                "address": identity["address"],
                "host_name": identity["host"],
                "photo_url": identity["photo"],
                "image_url": identity["photo"],
                "email": identity["email"],
            },
        ),
    ]
    for adapter_id, payload in plan:
        try:
            resp = dispatch(inv_id, adapter_id, payload)
            rid = resp["run_id"]
            result.runs[rid] = RunResult(adapter_id=adapter_id, run_id=rid)
            print(f"  [{label}] dispatch {adapter_id:8s} run={rid[:8]}")
        except httpx.HTTPStatusError as e:
            result.breaks.append(
                f"DISPATCH_HTTP {adapter_id}: {e.response.status_code} {e.response.text[:200]}"
            )
            print(
                f"  [{label}] [BREAK] dispatch {adapter_id} HTTP {e.response.status_code}: {e.response.text[:200]}"
            )
        except Exception as e:
            result.breaks.append(f"DISPATCH_FAIL {adapter_id}: {type(e).__name__}: {e}")
            print(f"  [{label}] [BREAK] dispatch {adapter_id} {type(e).__name__}: {e}")
        time.sleep(0.15)

    print(f"  [{label}] waiting {wait_seconds}s for events ...")
    time.sleep(wait_seconds)
    stop_evt.set()
    time.sleep(0.5)

    # Per-run analysis: any 0-event run is a break; any all-error run is a break.
    for rid, run in result.runs.items():
        if not run.events:
            result.breaks.append(f"ZERO_EVENTS: {run.adapter_id} run={rid[:8]}")
        elif (
            not run.has_real_result
            and run.error_count > 0
            and len(run.events) <= run.error_count + 2
        ):
            # accepted + N errors but no result -> chain broke for this workflow
            result.breaks.append(
                f"ALL_ERRORS: {run.adapter_id} run={rid[:8]} errors={run.error_count}"
            )

    return result


def summarize(results: list[IterationResult]) -> int:
    print("\n" + "=" * 72)
    print("STRESS HARNESS SUMMARY")
    print("=" * 72)
    total_breaks = 0
    all_real_errors: list[str] = []
    for r in results:
        print(
            f"\n[{r.identity_label}] inv={r.investigation_id[:8]} "
            f"events={r.total_events} findings={r.finding_counts}"
        )
        for rid, run in r.runs.items():
            print(f"  workflow {run.adapter_id:8s} dispatched=run-{rid[:8]}")
        if r.real_errors:
            print(f"  REAL ADAPTER ERRORS ({len(r.real_errors)}):")
            for e in r.real_errors:
                print(f"    - {e}")
            all_real_errors.extend(r.real_errors)
        if r.breaks:
            total_breaks += len(r.breaks)
            print(f"  [BREAKS:{len(r.breaks)}]")
            for b in r.breaks:
                print(f"    - {b}")
    # Categorize real errors across all iterations.
    if all_real_errors:
        print("\n--- ERROR CATEGORIES (across all iterations) ---")
        cat: dict = defaultdict(int)
        for e in all_real_errors:
            cat[e.split(":")[0]] += 1
        for adapter, count in sorted(cat.items(), key=lambda x: -x[1]):
            print(f"  {count:3d}x  {adapter}")
    print(f"\nTOTAL BREAKS: {total_breaks} across {len(results)} iterations")
    return total_breaks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=3)
    ap.add_argument("--wait", type=int, default=45, help="seconds to wait per iteration for events")
    ap.add_argument("--stop-on-break", action="store_true")
    args = ap.parse_args()

    results: list[IterationResult] = []
    for i in range(args.iterations):
        identity = IDENTITIES[i % len(IDENTITIES)]
        try:
            r = run_iteration(identity, wait_seconds=args.wait)
        except Exception as e:
            print(f"[iter {i}] FATAL: {type(e).__name__}: {e}")
            return 3
        results.append(r)
        if args.stop_on_break and r.breaks:
            print(f"\n[iter {i}] stop-on-break: {len(r.breaks)} break(s) found, halting.")
            break

    breaks = summarize(results)
    return 0 if breaks == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
