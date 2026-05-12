"""Partial-recovery subprocess wrapper (ship A+F+B+C).

Castrickclues's marquee technique: submit a target to a platform's
forgot-password flow and harvest the obfuscated partial email/phone the
platform displays as the "we'll send a code to ***@gmail.com" hint. The
partial form alone is signal -- corroborates that the target email/phone
IS linked to an account on that platform.

Platforms supported (selected by OSINT_PARTIAL_PLATFORM env var):
  - microsoft  account.live.com password reset       (Ship A; phone -> partial email)
  - linkedin   linkedin.com password reset             (Ship F; email -> partial)
  - instagram  instagram.com password reset            (Ship B; email/username -> partial)
  - twitter    twitter.com password reset              (Ship C; email -> partial)

Contract (Sora ADR-0004 sec.5):
  stdin:   {"target": "..."} on one line, EOF
  env:     OSINT_PARTIAL_PLATFORM=<microsoft|linkedin|instagram|twitter>
           OSINT_ADAPTER_MODE=synthetic   (optional; skips browser)
           OSINT_PARTIAL_REGION=EU         (optional; gates on lawful-basis flag)
           OSINT_PARTIAL_LAWFUL_BASIS_CONFIRMED=1
             (required if region=EU; investigator's documented Art. 6(1)(f)
              legitimate-interest basis for processing EU-subject data)
           OSINT_PARTIAL_KEEP_VALUES=1     (optional; default 0 = redact
              partial-email/phone strings before emit. Naomi #3)
           OSINT_DATA_ROOT                (optional; overrides where
              rate-limit + audit files land; default <repo>/data/)
  stdout:  NDJSON events
  stderr:  log lines
  exit:    0 on clean run; non-zero on adapter failure

Wire shape (default; OSINT_PARTIAL_KEEP_VALUES unset or 0):
  - one `person-match` per partial visible, with source=<platform>_partial,
    target, account_exists boolean, and `email_partial_meta` /
    `phone_partial_meta` (first char + local-part length + domain),
    NOT the raw partial string.
  - one `tool-run-result` summary with partials_visible_count,
    account_signal, parsed flag.

With OSINT_PARTIAL_KEEP_VALUES=1 (live investigator review only):
  - person-match additionally carries `email_partial` / `phone_partial`
    raw strings as the platform displayed them. Do not persist these
    into dossier exports; this mode is for in-session review.

Operational hardening (Naomi 2026-05-11):
  - Per-platform rate-limit at the wrapper layer (30-45s with random
    jitter). Persists across subprocess invocations via a lockfile.
  - Per-query audit log written to data/partial-pivots-audit/<date>.jsonl
    so the investigator can prove single-target investigative use.
  - EU-target guardrail requires OSINT_PARTIAL_LAWFUL_BASIS_CONFIRMED=1.
  - Partial *values* redacted by default; only the *fact* of account
    existence + partial metadata (length + domain) emitted.

Discipline: account-existence enumeration on investigative targets with
a documented basis, per-target only. Bulk discovery is misuse. Per the
user's 2026-05-11 scope note: articulating-link investigation, not
stalkerware.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_RATE_LIMIT_BASE_S = 30.0
_RATE_LIMIT_JITTER_S = 15.0


def _emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _data_dir() -> Path:
    """Resolved data root. Tests override via OSINT_DATA_ROOT."""
    override = os.environ.get("OSINT_DATA_ROOT", "").strip()
    if override:
        return Path(override)
    # adapters/partial_recovery/wrapper.py -> ../../data/
    return Path(__file__).resolve().parents[2] / "data"


def _keep_values() -> bool:
    """Naomi #3: default to redacting partial values; opt-in keeps them."""
    return os.environ.get("OSINT_PARTIAL_KEEP_VALUES", "0").strip() == "1"


def _enforce_rate_limit(platform: str) -> None:
    """Naomi #1+#2: per-platform rate-limit at the wrapper layer with
    random jitter. Persists across subprocess invocations via a lockfile.

    Skipped entirely in synthetic mode (callers control timing in tests).
    """
    if os.environ.get("OSINT_ADAPTER_MODE", "").strip().lower() == "synthetic":
        return
    if os.environ.get("OSINT_PARTIAL_SKIP_RATE_LIMIT", "").strip() == "1":
        # Test-only escape hatch. Never set this for live runs.
        return
    dir_ = _data_dir() / "partial-pivots-rate-limit"
    try:
        dir_.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(f"rate-limit dir create failed (will proceed): {exc}\n")
        return
    lock = dir_ / f"{platform}.last"
    now = time.time()
    last = 0.0
    if lock.is_file():
        try:
            last = float(lock.read_text().strip())
        except (ValueError, OSError):
            last = 0.0
    elapsed = now - last
    required = _RATE_LIMIT_BASE_S + random.uniform(0.0, _RATE_LIMIT_JITTER_S)
    if elapsed < required:
        delay = required - elapsed
        sys.stderr.write(
            f"rate-limit: sleeping {delay:.1f}s before {platform} query "
            f"(min {_RATE_LIMIT_BASE_S}s + jitter)\n"
        )
        time.sleep(delay)
    try:
        lock.write_text(str(time.time()))
    except OSError as exc:
        sys.stderr.write(f"rate-limit lock write failed (will proceed): {exc}\n")


def _check_region_guardrail() -> None:
    """Naomi #5: refuse EU-target queries without documented lawful basis."""
    region = os.environ.get("OSINT_PARTIAL_REGION", "").strip().upper()
    if region != "EU":
        return
    if os.environ.get("OSINT_PARTIAL_LAWFUL_BASIS_CONFIRMED", "").strip() == "1":
        return
    _emit(
        {
            "event_type": "tool-run-error",
            "payload": {
                "reason": (
                    "OSINT_PARTIAL_REGION=EU set without "
                    "OSINT_PARTIAL_LAWFUL_BASIS_CONFIRMED=1. Document an "
                    "Art. 6(1)(f) legitimate-interest basis before processing "
                    "EU-subject data; set the env var to confirm."
                ),
            },
        }
    )
    sys.exit(5)


def _audit_log(platform: str, target: str, account_signal: str) -> None:
    """Naomi #4: per-query audit trail (target, platform, signal, ts).
    NEVER includes partial values -- the audit is about what was queried,
    not what was harvested. Append-only NDJSON keyed by UTC date."""
    if os.environ.get("OSINT_ADAPTER_MODE", "").strip().lower() == "synthetic":
        return
    dir_ = _data_dir() / "partial-pivots-audit"
    try:
        dir_.mkdir(parents=True, exist_ok=True)
        path = dir_ / f"{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
        line = json.dumps(
            {
                "ts": _now_iso(),
                "platform": platform,
                "target": target,
                "account_signal": account_signal,
            },
            separators=(",", ":"),
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        sys.stderr.write(f"audit log write failed (will proceed): {exc}\n")


def _redact_email_partial(raw: str) -> dict[str, Any]:
    """Strip the asterisk-leak in a redacted-email string while keeping
    the durable signal: first char, local-part length, domain.

    `j***@gmail.com` -> {first: "j", local_length: 4, domain: "gmail.com"}
    """
    out: dict[str, Any] = {"raw_length": len(raw)}
    if "@" not in raw:
        return out
    local, _, domain = raw.partition("@")
    out["domain"] = domain
    if local:
        out["first"] = local[0] if local[0] != "*" else ""
        # Length includes all chars (the platform's asterisks count too);
        # this is the most we can infer about the real local-part length.
        out["local_length"] = len(local)
    return out


def _redact_phone_partial(raw: str) -> dict[str, Any]:
    """Phone partials are usually 'ending in 1234' style. Keep the
    trailing digits (the most distinctive bit) and the implied length."""
    digits = re.findall(r"\d", raw)
    return {
        "raw_length": len(raw),
        "last_digits": "".join(digits[-4:]) if digits else "",
        "implied_length": len(digits) + raw.count("*") + raw.count("•"),
    }


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
    target = (
        payload.get("target")
        or payload.get("email")
        or payload.get("phone")
        or "user@example.com"
    )
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
    raw_partial = "s***c@e***le.com"
    person_payload: dict[str, Any] = {
        "source": f"{platform}_partial",
        "target": target,
        "account_exists": True,
        "email_partial_meta": _redact_email_partial(raw_partial),
        "synthetic": True,
    }
    if _keep_values():
        person_payload["email_partial"] = raw_partial
    _emit({"event_type": "person-match", "payload": person_payload})
    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": f"{platform}_partial",
                "target": target,
                "partials_visible_count": 1,
                "account_signal": "exists",
                "values_kept": _keep_values(),
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
    # Naomi #5: refuse EU-target queries without documented lawful basis.
    # Runs BEFORE anything else so a misconfigured env doesn't trigger a
    # browser launch + a real outbound request.
    _check_region_guardrail()

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

    # Naomi #1+#2: rate-limit at the wrapper layer with random jitter.
    # Skipped in synthetic mode (see _enforce_rate_limit).
    _enforce_rate_limit(platform)

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
    keep_values = _keep_values()
    total_visible = 0

    for kind, vals in partials.items():
        for v in vals[:5]:  # cap dossier noise per platform per kind
            total_visible += 1
            person_payload: dict[str, Any] = {
                "source": f"{platform}_partial",
                "target": target,
                "account_exists": signal == "exists",
            }
            # Naomi #3: emit metadata by default; raw partial only when
            # OSINT_PARTIAL_KEEP_VALUES=1 (live investigator-review only).
            if kind == "email":
                person_payload["email_partial_meta"] = _redact_email_partial(v)
                if keep_values:
                    person_payload["email_partial"] = v
            elif kind == "phone":
                person_payload["phone_partial_meta"] = _redact_phone_partial(v)
                if keep_values:
                    person_payload["phone_partial"] = v
            _emit({"event_type": "person-match", "payload": person_payload})

    # Naomi #4: per-query audit trail. Never includes partial values.
    _audit_log(platform, target, signal)

    _emit(
        {
            "event_type": "tool-run-result",
            "payload": {
                "source": f"{platform}_partial",
                "target": target,
                "partials_visible_count": total_visible,
                "account_signal": signal,
                "parsed": bool(partials) or signal in ("exists", "missing"),
                "values_kept": keep_values,
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
