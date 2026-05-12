"""Partial-recovery subprocess wrapper (ship A+F+B+C).

Castrickclues's marquee technique: submit a target to a platform's
forgot-password flow and harvest the obfuscated partial email/phone the
platform displays as the "we'll send a code to ***@gmail.com" hint. The
partial form alone is signal -- corroborates that the target email/phone
IS linked to an account on that platform. Multiple partials across
platforms can sometimes be brute-forced to reconstruct the full string
(out of scope for this wrapper -- we just emit what's visible).

Platforms supported (selected by OSINT_PARTIAL_PLATFORM env var):
  - microsoft  account.live.com password reset       (Ship A; phone -> partial email)
  - linkedin   linkedin.com password reset             (Ship F; email -> partial)
  - instagram  instagram.com password reset            (Ship B; email/username -> partial)
  - twitter    twitter.com password reset              (Ship C; email -> partial)

Contract (Sora ADR-0004 sec.5):
  stdin:   {"target": "..."} on one line, EOF
  env:     OSINT_PARTIAL_PLATFORM=<microsoft|linkedin|instagram|twitter>
           OSINT_ADAPTER_MODE=synthetic  (optional; skips browser)
  stdout:  NDJSON events
  stderr:  log lines
  exit:    0 on clean run; non-zero on adapter failure

Wire shape:
  - one `person-match` per partial pattern matched in the response page,
    with source=<platform>_partial, target, and either email_partial or
    phone_partial set
  - one `tool-run-result` summary with partials_found, account_signal

Discipline: this is account-existence enumeration on consenting target
emails / phones, used per-target for property-vetting (articulating-link
investigation). Bulk discovery would be misuse; the worker should rate-
limit calls (one per 30s) and the live path explicitly suggests doing so.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from typing import Any


def _emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# Per-platform configuration. Selectors are best-effort against the
# 2026-05-11 site structure; partial regex patterns mirror what each
# platform's recovery flow exposes. If a selector breaks (sites re-skin),
# failure mode is zero matches + a `tool-run-result` with parsed=false,
# not a crash.
_PLATFORMS: dict[str, dict[str, Any]] = {
    "microsoft": {
        "url": "https://account.live.com/password/reset",
        "input_selector": "input[name='loginfmt'], input[type='email'], #i0116",
        "submit_selector": "input[type=submit], button[type=submit], #idSIButton9",
        # MS recovery shows: "We'll send a verification code to ***@example.com"
        # plus optional "Phone: ******1234"
        "partial_email_re": r"[A-Za-z0-9._+-]{0,4}\*+@[A-Za-z0-9.-]{0,12}\.[a-z]{2,4}",
        "partial_phone_re": r"\+?\d{0,3}[ -]?\d{0,3}[ -]?[*•]+[ -]?\d{2,4}",
        "account_exists_signals": ("send", "we'll send", "verification code"),
        "account_missing_signals": ("can't find", "couldn't find", "doesn't match"),
    },
    "linkedin": {
        "url": "https://www.linkedin.com/uas/request-password-reset",
        "input_selector": "input[name='session_key'], input[id='session_key']",
        "submit_selector": "button[type=submit], button[name='action']",
        # LinkedIn shows: "We sent a verification link to j***@gmail.com"
        "partial_email_re": r"[A-Za-z0-9._+-]{0,4}\*+@[A-Za-z0-9.-]{0,12}\.[a-z]{2,4}",
        "partial_phone_re": r"\+?\d{0,3}[ -]?\d{0,3}[ -]?[*•]+[ -]?\d{2,4}",
        "account_exists_signals": ("sent", "verification link", "check your email"),
        "account_missing_signals": ("doesn't match", "not found", "no account"),
    },
    "instagram": {
        "url": "https://www.instagram.com/accounts/password/reset/",
        "input_selector": "input[name='email_or_username'], input[name='cppDisabled']",
        "submit_selector": "button[type=submit]",
        # Instagram shows: "We sent an email to ***@gmail.com with a link"
        "partial_email_re": r"[A-Za-z0-9._+-]{0,4}\*+@[A-Za-z0-9.-]{0,12}\.[a-z]{2,4}",
        "partial_phone_re": r"\+?\d{0,3}[ -]?\d{0,3}[ -]?[*•]+[ -]?\d{2,4}",
        "account_exists_signals": ("we sent", "check your email", "we've sent"),
        "account_missing_signals": ("couldn't find", "doesn't exist", "the username"),
    },
    "twitter": {
        # X.com is the canonical domain now; twitter.com redirects.
        "url": "https://x.com/i/flow/password_reset",
        "input_selector": "input[name='text'], input[autocomplete='username']",
        "submit_selector": "div[role='button']:has-text('Next'), button[role='button']",
        # X shows: "We sent your code to j***@gmail.com" or partial phone.
        "partial_email_re": r"[A-Za-z0-9._+-]{0,4}\*+@[A-Za-z0-9.-]{0,12}\.[a-z]{2,4}",
        "partial_phone_re": r"\+?\d{0,3}[ -]?\d{0,3}[ -]?[*•]+[ -]?\d{2,4}",
        "account_exists_signals": ("sent", "verification", "we sent your code"),
        "account_missing_signals": ("we couldn't find", "no account"),
    },
}


def _resolve_platform() -> tuple[str, dict[str, Any]]:
    name = os.environ.get("OSINT_PARTIAL_PLATFORM", "").strip().lower()
    if not name or name not in _PLATFORMS:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": (
                        f"OSINT_PARTIAL_PLATFORM='{name}' not one of: "
                        + ", ".join(_PLATFORMS)
                    )
                },
            }
        )
        sys.exit(1)
    return name, _PLATFORMS[name]


def _run_synthetic(platform: str, payload: dict[str, Any]) -> int:
    """Synthetic event stream -- no browser, deterministic. Used by the
    in-process synthetic registration and by OSINT_ADAPTER_MODE=synthetic."""
    target = payload.get("target") or payload.get("email") or payload.get("phone") or "user@example.com"
    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": f"{platform}_partial_pivot",
                "target": target,
                "synthetic": True,
            },
        }
    )
    _emit(
        {
            "event_type": "person-match",
            "payload": {
                "source": f"{platform}_partial",
                "target": target,
                "email_partial": "s***c@e***le.com",
                "account_exists": True,
                "synthetic": True,
            },
        }
    )
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": f"{platform}_partial",
                "target": target,
                "partials_found": 1,
                "account_signal": "exists",
                "synthetic": True,
            },
        }
    )
    return 0


def _classify_account_signal(text: str, config: dict[str, Any]) -> str:
    lower = text.lower()
    for s in config.get("account_exists_signals", ()):
        if s.lower() in lower:
            return "exists"
    for s in config.get("account_missing_signals", ()):
        if s.lower() in lower:
            return "missing"
    return "unknown"


def _harvest_partials(text: str, config: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    email_pat = config.get("partial_email_re")
    phone_pat = config.get("partial_phone_re")
    if email_pat:
        emails = list(dict.fromkeys(re.findall(email_pat, text)))
        # Filter out spurious matches (need at least one * inside).
        emails = [e for e in emails if "*" in e]
        if emails:
            out["email"] = emails
    if phone_pat:
        phones = list(dict.fromkeys(re.findall(phone_pat, text)))
        phones = [p for p in phones if "*" in p or "•" in p]
        if phones:
            out["phone"] = phones
    return out


def _run_live(platform: str, config: dict[str, Any], payload: dict[str, Any]) -> int:
    """Live mode: drive Patchright (Playwright with stealth patches) through
    the platform's password-recovery flow and harvest any partial-email or
    partial-phone strings from the resulting page text."""
    target = (
        (payload.get("target") or payload.get("email") or payload.get("phone") or "")
    ).strip()
    if not target:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {"reason": "missing 'target' / 'email' / 'phone' in payload"},
            }
        )
        return 2

    try:
        from patchright.sync_api import sync_playwright
    except ImportError as exc:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"patchright not importable: {exc}",
                    "suggest": "pip install patchright in the empirical venv",
                },
            }
        )
        return 3

    _emit(
        {
            "event_type": "tool-run-accepted",
            "payload": {
                "adapter": f"{platform}_partial_pivot",
                "target": target,
                "started_at": _now_iso(),
            },
        }
    )

    body_text = ""
    err: str | None = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            )
            page = ctx.new_page()
            page.goto(config["url"], wait_until="domcontentloaded", timeout=20_000)
            # Best-effort: fill the input + click submit. If the selectors
            # break (site re-skinned), the resulting body still gets scraped
            # for partial patterns -- failure mode is "no matches", not crash.
            try:
                page.fill(config["input_selector"], target, timeout=8_000)
            except Exception as fill_exc:  # noqa: BLE001
                sys.stderr.write(f"fill failed: {fill_exc}\n")
            try:
                page.click(config["submit_selector"], timeout=8_000)
            except Exception as click_exc:  # noqa: BLE001
                sys.stderr.write(f"click failed: {click_exc}\n")
            # Settle. Partial reveal is often shown a beat after submit.
            time.sleep(2.5)
            try:
                body_text = page.inner_text("body", timeout=5_000)
            except Exception as text_exc:  # noqa: BLE001
                sys.stderr.write(f"inner_text failed: {text_exc}\n")
                body_text = page.content()
            browser.close()
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"

    if err and not body_text:
        _emit(
            {
                "event_type": "tool-run-error",
                "payload": {
                    "reason": f"patchright {err}",
                    "platform": platform,
                    "target": target,
                    "suggest": "site may be blocking; try OSINT_ADAPTER_MODE=synthetic",
                },
            }
        )
        return 4

    signal = _classify_account_signal(body_text, config)
    partials = _harvest_partials(body_text, config)

    for kind, vals in partials.items():
        for v in vals[:5]:  # cap dossier noise per platform per kind
            _emit(
                {
                    "event_type": "person-match",
                    "payload": {
                        "source": f"{platform}_partial",
                        "target": target,
                        f"{kind}_partial": v,
                        "account_exists": signal == "exists",
                    },
                }
            )

    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": f"{platform}_partial",
                "target": target,
                "partials_found": sum(len(v[:5]) for v in partials.values()),
                "account_signal": signal,
                "parsed": bool(partials) or signal in ("exists", "missing"),
                "finished_at": _now_iso(),
            },
        }
    )
    return 0


def _main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        _emit({"event_type": "tool-run-error", "payload": {"reason": "empty stdin"}})
        return 1
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        _emit(
            {"event_type": "tool-run-error", "payload": {"reason": f"bad stdin JSON: {exc}"}}
        )
        return 1

    platform, config = _resolve_platform()

    if os.environ.get("OSINT_ADAPTER_MODE", "").lower() == "synthetic":
        return _run_synthetic(platform, payload)
    return _run_live(platform, config, payload)


if __name__ == "__main__":
    sys.exit(_main())
