"""Default User-Agent for adapter HTTP clients.

Per W4-UA (Margaret wave-4 roadmap §3): adapters that talk to TARGET
webservers (not infrastructure DoH/RDAP) should default to a current
Chrome-on-Win11 string so the target's access log doesn't attribute
the probe back to the operator. Set OSINT_TRANSPARENT_UA=1 to opt in
to the literal osint-goblin string for operators who explicitly want
transparency. The opsec runbook (Sprint-1 C-1) documents the choice.

severity_basis: matrix:PV_OPSEC_UA_SELF_IDENTIFICATION
"""

from __future__ import annotations

import os

# Current Chrome stable on Windows 11 (UA string as of 2026-05).
_CHROME_WIN11_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)

_TRANSPARENT_UA = "osint-goblin/0.1 (https://github.com/local; personal-investigator)"


def default_ua() -> str:
    """Return the default User-Agent string for adapter HTTP clients.

    OSINT_TRANSPARENT_UA=1 in env returns the transparent literal.
    Anything else (default) returns the Chrome-Win11 string.
    """
    if os.environ.get("OSINT_TRANSPARENT_UA") == "1":
        return _TRANSPARENT_UA
    return _CHROME_WIN11_UA
