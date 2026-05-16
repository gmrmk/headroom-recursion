"""Standalone probe: try the 3 free bypass stacks against VRBO + TripAdvisor.

Runs each tool with its built-in stealth knobs and records what happens.
Use this BEFORE wiring any tool into humanize.py -- only invest in
integration for stacks that actually defeat the challenge.

Each cell:
  - fresh process state
  - challenge body saved to tools/dev/bypass-probe-bodies/
  - status inferred from body markers
"""

from __future__ import annotations

import asyncio
import time
import traceback
from pathlib import Path

_OUT_DIR = Path(__file__).resolve().parent / "bypass-probe-bodies"
_OUT_DIR.mkdir(exist_ok=True)


_MARKERS = (
    ("Bot or Not", "Imperva Bot or Not", 429),
    ("captcha-delivery.com", "DataDome", 403),
    ("Pardon Our Interruption", "Akamai BMA", 403),
    ("Just a moment", "Cloudflare interstitial", 503),
    ("Ray ID", "Cloudflare challenge", 503),
)


def interpret(body: str) -> tuple[str, int]:
    head = body[:6000] if isinstance(body, str) else ""
    for needle, label, code in _MARKERS:
        if needle in head:
            return (f"BLOCKED via {label}", code)
    if len(body) > 50_000:
        return ("OK -- real page", 200)
    return (f"suspicious ({len(body)}b)", 200)


def save(stack: str, platform: str, body: str, status: int) -> str:
    p = _OUT_DIR / f"{platform}-{stack}-status{status}.html"
    try:
        p.write_text(body, encoding="utf-8")
        return str(p)
    except Exception:
        return ""


# ----------------------------------------------------------------------------
# Stack 1: Botasaurus (sync; has detect_and_bypass_cloudflare + human mode)
# ----------------------------------------------------------------------------


def probe_botasaurus(url: str, platform: str) -> dict:
    out: dict = {"stack": "botasaurus", "platform": platform}
    t0 = time.monotonic()
    try:
        from botasaurus_driver import Driver

        driver = Driver(headless=True, wait_for_complete_page_load=True)
        try:
            driver.get(url, bypass_cloudflare=True)
            time.sleep(3)  # let Imperva/DataDome solver finish
            body = driver.page_html
            interp, status = interpret(body)
            out["status"] = status
            out["body_len"] = len(body)
            out["interpretation"] = interp
            out["saved_to"] = save("botasaurus", platform, body, status)
        finally:
            driver.close()
    except Exception:
        out["exception"] = traceback.format_exc()[-300:]
    out["elapsed_s"] = round(time.monotonic() - t0, 1)
    return out


# ----------------------------------------------------------------------------
# Stack 2: Zendriver (async; CDP-direct, no Playwright Runtime.Enable leak)
# ----------------------------------------------------------------------------


def probe_zendriver(url: str, platform: str) -> dict:
    out: dict = {"stack": "zendriver", "platform": platform}
    t0 = time.monotonic()

    async def _run() -> tuple[int, str]:
        import zendriver as zd

        browser = await zd.start(headless=True)
        try:
            tab = await browser.get(url)
            await asyncio.sleep(5)  # generous: let challenge JS execute + redirect
            body = await tab.get_content()
            return (200, body)  # status inferred later
        finally:
            await browser.stop()

    try:
        status, body = asyncio.run(_run())
        interp, status = interpret(body)
        out["status"] = status
        out["body_len"] = len(body)
        out["interpretation"] = interp
        out["saved_to"] = save("zendriver", platform, body, status)
    except Exception:
        out["exception"] = traceback.format_exc()[-300:]
    out["elapsed_s"] = round(time.monotonic() - t0, 1)
    return out


# ----------------------------------------------------------------------------
# Stack 3: NoDriver (async; older sibling of Zendriver)
# ----------------------------------------------------------------------------


def probe_nodriver(url: str, platform: str) -> dict:
    out: dict = {"stack": "nodriver", "platform": platform}
    t0 = time.monotonic()

    async def _run() -> tuple[int, str]:
        import nodriver as nd

        browser = await nd.start(headless=True)
        try:
            tab = await browser.get(url)
            await asyncio.sleep(5)
            body = await tab.get_content()
            return (200, body)
        finally:
            browser.stop()

    try:
        status, body = asyncio.run(_run())
        interp, status = interpret(body)
        out["status"] = status
        out["body_len"] = len(body)
        out["interpretation"] = interp
        out["saved_to"] = save("nodriver", platform, body, status)
    except Exception:
        out["exception"] = traceback.format_exc()[-300:]
    out["elapsed_s"] = round(time.monotonic() - t0, 1)
    return out


def main() -> None:
    targets = [
        ("vrbo", "https://www.vrbo.com/1682245"),
        ("tripadvisor", "https://www.tripadvisor.com/VacationRentals"),
    ]
    probes = [
        ("botasaurus", probe_botasaurus),
        ("zendriver", probe_zendriver),
        ("nodriver", probe_nodriver),
    ]

    print(f"Saving response bodies to: {_OUT_DIR}\n")
    rows: list[dict] = []
    for platform, url in targets:
        print(f"=== {platform} @ {url} ===")
        for name, fn in probes:
            print(f"  [{name:11}] running...", end=" ", flush=True)
            r = fn(url, platform)
            if "exception" in r:
                print(f"EXCEPTION ({r['exception'].splitlines()[-1][:80]})")
            else:
                print(
                    f"status={r.get('status', '?')} "
                    f"len={r.get('body_len', '?'):>8} "
                    f"-> {r.get('interpretation', '?')}"
                )
            rows.append(r)
        print()

    print("========== SUMMARY ==========")
    for r in rows:
        if "exception" in r:
            print(f"  {r['platform']:12} {r['stack']:11}  EXCEPTION")
        else:
            print(
                f"  {r['platform']:12} {r['stack']:11}  "
                f"status={r['status']:>3} "
                f"len={r['body_len']:>8} "
                f"elapsed={r['elapsed_s']:>5}s  {r['interpretation']}"
            )


if __name__ == "__main__":
    main()
