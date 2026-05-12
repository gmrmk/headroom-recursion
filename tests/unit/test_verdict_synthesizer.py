"""Lock Margaret's free-stack rubric (2026-05-11 deliberation).

The verdict synthesizer in `tools/dev/smoke-w11-em.py` reduces W11.em+
per-adapter event counts to one of six investigator-actionable buckets.
These tests pin the rules so they don't silently drift with later
adapter changes -- the rubric is the *contract* for how raw event counts
map to a property-vetting verdict.

Empirical anchors (verified live 2026-05-11):
  - torvalds@linux-foundation.org  -> real-careful
  - test@example.com               -> compromised-real
  - low-footprint pattern (everything 0) -> low-footprint
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
_SMOKE_PATH = _REPO_ROOT / "tools" / "dev" / "smoke-w11-em.py"


def _import_smoke_module():
    """The smoke script lives at tools/dev/ -- import by file path rather
    than relying on it being on sys.path. We reach into it to test the
    private rubric without committing to a public package surface."""
    spec = importlib.util.spec_from_file_location("smoke_w11_em", _SMOKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["smoke_w11_em"] = module
    spec.loader.exec_module(module)
    return module


def _make_per_adapter(
    *,
    gravatar_pm: int = 0,
    github_pm: int = 0,
    user_scanner_pm: int = 0,
    hudson_rock_bh: int = 0,
    hibp_bh: int = 0,
) -> list[tuple[str, float, dict[str, int]]]:
    """Build the per-adapter counts shape the synthesizer consumes."""

    def counts(events: int, **kw: int) -> dict[str, int]:
        d = {"events": events, "errors": 0, "person_match": 0, "breach_hit": 0}
        d.update(kw)
        return d

    return [
        ("email_mx_validate", 0.1, counts(1)),
        ("hibp_breach_check", 0.5, counts(1 + hibp_bh, breach_hit=hibp_bh)),
        ("gravatar_profile_lookup", 0.5, counts(1 + gravatar_pm, person_match=gravatar_pm)),
        ("github_commit_email_search", 1.0, counts(1 + github_pm, person_match=github_pm)),
        ("hudson_rock_email_check", 1.0, counts(1 + hudson_rock_bh, breach_hit=hudson_rock_bh)),
        ("user_scanner", 20.0, counts(2 + user_scanner_pm, person_match=user_scanner_pm)),
    ]


def test_real_careful_pattern_anchors_torvalds_case() -> None:
    """Torvalds's actual smoke (2026-05-11): Gravatar yes + GitHub yes +
    Hudson Rock 0 + user-scanner 0 -> real-careful."""
    smoke = _import_smoke_module()
    per_adapter = _make_per_adapter(gravatar_pm=0, github_pm=4)  # Gravatar via display_name not pm
    # The Torvalds case had Gravatar profile_found=true but no verified_accounts,
    # so person_match=0. Identity signal in our rubric requires gravatar OR github.
    verdict = smoke._synthesize_verdict(per_adapter)
    assert verdict["bucket"] == "real-careful"
    assert verdict["confidence"] == "high"


def test_compromised_real_pattern_anchors_test_example_case() -> None:
    """test@example.com smoke (2026-05-11): GitHub yes + Hudson Rock yes
    (5 stealers) -> compromised-real."""
    smoke = _import_smoke_module()
    per_adapter = _make_per_adapter(github_pm=1, hudson_rock_bh=5)
    verdict = smoke._synthesize_verdict(per_adapter)
    assert verdict["bucket"] == "compromised-real"
    assert verdict["confidence"] == "high"


def test_low_footprint_pattern_when_every_signal_zero() -> None:
    """Email with zero hits across every leg -> low-footprint. Investigator
    advice: 'email gives no identity bridge; pivot to other primitives'."""
    smoke = _import_smoke_module()
    per_adapter = _make_per_adapter()  # All zeros
    verdict = smoke._synthesize_verdict(per_adapter)
    assert verdict["bucket"] == "low-footprint"
    assert verdict["confidence"] == "medium"
    assert "pivot" in verdict["next"].lower()


def test_suspicious_churn_pattern_compromise_without_identity() -> None:
    """Zero identity + zero behavior + Hudson Rock compromise hits ->
    suspicious-churn. This is the 'fraudster fingerprint' bucket."""
    smoke = _import_smoke_module()
    per_adapter = _make_per_adapter(hudson_rock_bh=3)
    verdict = smoke._synthesize_verdict(per_adapter)
    assert verdict["bucket"] == "suspicious-churn"


def test_real_active_when_identity_behavior_and_consumer_tail() -> None:
    """Typical developer profile: Gravatar + GitHub + user-scanner hits,
    no compromise -> real-active (distinct from real-careful because the
    consumer-service tail is non-zero)."""
    smoke = _import_smoke_module()
    per_adapter = _make_per_adapter(gravatar_pm=2, github_pm=10, user_scanner_pm=5)
    verdict = smoke._synthesize_verdict(per_adapter)
    assert verdict["bucket"] == "real-active"


def test_compromised_real_overrides_real_careful_when_both_present() -> None:
    """If a real-careful pattern also has compromise hits, the verdict
    should be compromised-real (compromise context is the more actionable
    note for the investigator). Rule precedence test."""
    smoke = _import_smoke_module()
    per_adapter = _make_per_adapter(gravatar_pm=2, github_pm=10, hudson_rock_bh=1)
    verdict = smoke._synthesize_verdict(per_adapter)
    assert verdict["bucket"] == "compromised-real"


def test_signals_are_derived_from_event_counts_only() -> None:
    """The rubric must not depend on raw upstream fields -- only on the
    rolled-up event-type counts (person_match, breach_hit). This keeps
    the synthesis stable when adapters reshape their payloads."""
    smoke = _import_smoke_module()
    per_adapter = _make_per_adapter(gravatar_pm=1)
    signals = smoke._build_signals(per_adapter)
    assert signals["identity"] is True
    assert signals["behavior"] is False
    assert signals["compromise"] is False
    assert signals["consumer_tail"] is False
