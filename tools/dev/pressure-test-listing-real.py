"""Live pressure test: real listing-body fetches for 4 bespoke parsers.

Sprint Step D, 2026-05-16. The 4 newly-committed parsers (VRBO,
TripAdvisor, Yanolja, Leboncoin) were verified against synthetic
fixtures only. This script closes that gap: one real fetch per
platform, routed through the recommended humanize tier, scored against
the cross-platform contract.

Cross-platform contract keys (the 7 the dossier UI relies on):
  title, price, location, photos, host, jurisdiction, id

Decisions locked at advisor checkpoint:
  - One URL per platform; no T1-T5 matrix.
  - Save body BEFORE parsing (evidence preservation).
  - Fresh HumanizedFetcher per platform (no context bleed).
  - Field mapping: price -> nightly_price OR price_range_text,
    location -> address_displayed (require non-empty city+country),
    photos -> photo_urls non-empty list, host -> host_name,
    jurisdiction -> country, id -> listing_id.
  - "Missing" documented-empty fields route to category (c)
    "platform genuinely doesn't expose" -- not parser bug.
  - Verdict: PASS w/ documented gaps OK; FAIL only on parser errors
    or drift in fields the parser claims to populate.

Naomi gate: real-body captures go to tools/dev/listing-failure-bodies/
(gitignored). Listings are public benign pages, not target-PII.
"""

from __future__ import annotations

import dataclasses
import os
import random
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKERS_SRC = _REPO_ROOT / "apps" / "workers" / "src"
if _WORKERS_SRC.exists():
    sys.path.insert(0, str(_WORKERS_SRC))

_BODY_DIR = _REPO_ROOT / "tools" / "dev" / "listing-failure-bodies"
_BODY_DIR.mkdir(parents=True, exist_ok=True)

_TODAY = "2026-05-16"

# Contract keys + which extractor field each maps to. Some platforms
# don't populate certain fields by design -- those become "documented
# gaps", scored as category (c) in the schema drift findings.
_CONTRACT_KEYS: tuple[str, ...] = (
    "title",
    "price",
    "location",
    "photos",
    "host",
    "jurisdiction",
    "id",
)

# Per-platform: which contract keys are documented to be empty in
# logged-out HTML (so a missing value is platform reality, not a parser
# bug). The script still reports them as "missing" but the verdict
# pipeline treats them as expected gaps.
_DOCUMENTED_GAPS: dict[str, set[str]] = {
    "vrbo": {"host", "price"},  # Expedia design; price lazy
    "tripadvisor": {"host", "price"},  # lazy GraphQL; price_range_text only
    "yanolja": {"host", "price"},  # KR convention; logged-out hides host
    "leboncoin": set(),  # classifieds; everything should populate
}


@dataclasses.dataclass
class PlatformProbe:
    name: str
    url: str
    recommended_tier: str
    fallback_tiers: tuple[str, ...]  # zendriver blocks -> stop


# One URL per platform. Picked for reproducibility:
#   - VRBO: a long-tenured vacation rental in Hilton Head (popular
#     destination so the listing is unlikely to be delisted soon)
#   - TripAdvisor: a famous London hotel (high traffic, stable URL)
#   - Yanolja: a Seoul hotel (stable listing on the KR platform)
#   - Leboncoin: a rental ad (URL pattern + ID, generic regional pick)
_PROBES: tuple[PlatformProbe, ...] = (
    PlatformProbe(
        name="vrbo",
        url="https://www.vrbo.com/1682245",
        recommended_tier="zendriver",
        fallback_tiers=(),  # zendriver is top; no higher tier
    ),
    PlatformProbe(
        name="tripadvisor",
        url="https://www.tripadvisor.com/Hotel_Review-g186338-d187591-Reviews-The_Ritz_London-London_England.html",
        recommended_tier="zendriver",
        fallback_tiers=(),
    ),
    PlatformProbe(
        name="yanolja",
        url="https://www.yanolja.com/places/3000034028",
        recommended_tier="patchright",
        fallback_tiers=("camoufox", "zendriver"),
    ),
    PlatformProbe(
        name="leboncoin",
        url="https://www.leboncoin.fr/locations/2960054428.htm",
        recommended_tier="patchright",
        fallback_tiers=("camoufox", "zendriver"),
    ),
)


@dataclasses.dataclass
class ContractScore:
    satisfied: list[str]
    partial: list[str]
    missing: list[str]

    def coverage(self) -> str:
        return f"{len(self.satisfied)} / {len(self.partial)} / {len(self.missing)}"


@dataclasses.dataclass
class ProbeResult:
    platform: str
    url: str
    tier_used: str
    fallback_chain: list[str]  # tiers tried in order
    status: int
    body_len: int
    elapsed_s: float
    extractor_output: dict | None
    score: ContractScore | None
    error: str | None
    body_path: Path | None


def _score_contract(platform: str, out: dict) -> ContractScore:
    """Score a parser output dict against the 7-key contract.

    Rules per key:
      - title: non-empty string -> satisfied
      - price: nightly_price (number) OR price_range_text (non-empty
        string) -> satisfied; partial if only one of {price_range_text,
        currency} is present without nightly_price
      - location: address_displayed non-empty AND both city+country
        present -> satisfied; partial if address_displayed has one but
        not the other
      - photos: photo_urls non-empty list -> satisfied; partial if just
        a single hero image (len==1, common for listing-page meta)
      - host: host_name non-empty -> satisfied
      - jurisdiction: country non-empty -> satisfied
      - id: listing_id non-empty -> satisfied
    """
    sat: list[str] = []
    par: list[str] = []
    mis: list[str] = []

    def _is_str_full(v) -> bool:
        return isinstance(v, str) and v.strip() != ""

    # title
    if _is_str_full(out.get("title")):
        sat.append("title")
    else:
        mis.append("title")

    # price
    nprice = out.get("nightly_price")
    prange = out.get("price_range_text") or ""
    cur = out.get("currency") or ""
    if isinstance(nprice, int | float) and nprice > 0:
        sat.append("price")
    elif _is_str_full(prange):
        # priceRange text from TripAdvisor counts as partial: it's a
        # free-text bucket, not a per-night number. Useful but not full
        # contract satisfaction.
        par.append("price")
    elif _is_str_full(cur):
        par.append("price")
    else:
        mis.append("price")

    # location
    addr = out.get("address_displayed") or ""
    city = out.get("city") or ""
    country = out.get("country") or ""
    if _is_str_full(addr) and _is_str_full(city) and _is_str_full(country):
        sat.append("location")
    elif _is_str_full(addr):
        par.append("location")
    else:
        mis.append("location")

    # photos
    photos = out.get("photo_urls") or []
    if isinstance(photos, list) and len(photos) >= 2:
        sat.append("photos")
    elif isinstance(photos, list) and len(photos) == 1:
        par.append("photos")
    else:
        mis.append("photos")

    # host
    if _is_str_full(out.get("host_name")):
        sat.append("host")
    else:
        mis.append("host")

    # jurisdiction
    if _is_str_full(out.get("country")):
        sat.append("jurisdiction")
    else:
        mis.append("jurisdiction")

    # id
    if _is_str_full(out.get("listing_id")):
        sat.append("id")
    else:
        mis.append("id")

    return ContractScore(satisfied=sat, partial=par, missing=mis)


def _try_tier(tier: str, probe: PlatformProbe) -> tuple[float, int, str, str | None]:
    """Single tier attempt. Returns (elapsed, status, body, error_or_None)."""
    from osint_goblin_workers.humanize import HumanizedFetcher

    os.environ["OSINT_BROWSER_TIER"] = tier

    t0 = time.monotonic()
    fetcher = HumanizedFetcher(investigation_id=f"step-d-{probe.name}")
    try:
        status, body = fetcher.fetch(
            probe.url,
            platform=probe.name,
            jitter=False,  # we add explicit inter-platform jitter below
            synthetic_interaction=True,
            timeout_s=120.0,
        )
        return (time.monotonic() - t0, status, body or "", None)
    except Exception as e:
        return (time.monotonic() - t0, 0, "", f"{type(e).__name__}: {e}")
    finally:
        try:
            fetcher.shred()
        except Exception:
            pass


def _is_block(status: int, body: str) -> bool:
    """Detect a hard block. Empty body + non-200 status, or known
    challenge markers in body."""
    if status != 200 or not body:
        return True
    body_head = body[:5000].lower()
    block_markers = (
        "bot or not",
        "pardon our interruption",
        "access denied",
        "just a moment",
        "captcha-delivery.com",
        "datadome",
        "g-recaptcha",
        "h-captcha",
        "ddos protection",
        "perimeterx",
        "are you a human",
    )
    if any(m in body_head for m in block_markers):
        return True
    return len(body) < 4_000


def _run_one(probe: PlatformProbe) -> ProbeResult:
    print(f"\n=== {probe.name.upper()} @ {probe.url} ===", flush=True)
    fallback_chain: list[str] = []
    elapsed_total = 0.0
    last_status = 0
    last_body = ""
    last_err: str | None = None

    ladder = (probe.recommended_tier, *probe.fallback_tiers)
    for tier in ladder:
        print(f"  [tier={tier}] fetching...", flush=True)
        elapsed, status, body, err = _try_tier(tier, probe)
        fallback_chain.append(tier)
        elapsed_total += elapsed
        print(
            f"    -> status={status} body_len={len(body)} elapsed={elapsed:.1f}s "
            f"err={err or '-'}",
            flush=True,
        )
        last_status = status
        last_body = body
        last_err = err
        if not _is_block(status, body):
            break
        if tier == ladder[-1]:
            print(f"    -> all tiers exhausted for {probe.name}; documenting block", flush=True)
            break
        time.sleep(random.uniform(3.0, 6.0))

    body_path: Path | None = None
    if last_body and last_status == 200:
        body_path = _BODY_DIR / f"{probe.name}-real-{_TODAY}.html"
        try:
            body_path.write_text(last_body, encoding="utf-8")
            print(f"    saved body -> {body_path.name}", flush=True)
        except Exception as e:
            print(f"    body save failed: {e}", flush=True)
            body_path = None

    extractor_output: dict | None = None
    score: ContractScore | None = None
    if last_status == 200 and last_body and not _is_block(last_status, last_body):
        try:
            extractor_output = _call_extractor(probe.name, last_body, probe.url)
            score = _score_contract(probe.name, extractor_output)
        except Exception as e:
            last_err = f"extractor: {type(e).__name__}: {e}"
            print(f"    extractor error: {last_err}", flush=True)

    return ProbeResult(
        platform=probe.name,
        url=probe.url,
        tier_used=fallback_chain[-1] if fallback_chain else "",
        fallback_chain=fallback_chain,
        status=last_status,
        body_len=len(last_body),
        elapsed_s=elapsed_total,
        extractor_output=extractor_output,
        score=score,
        error=last_err,
        body_path=body_path,
    )


def _call_extractor(platform: str, body: str, url: str) -> dict:
    from osint_goblin_workers.adapters_listing import (
        extract_leboncoin,
        extract_tripadvisor,
        extract_vrbo,
        extract_yanolja,
    )

    if platform == "vrbo":
        return extract_vrbo(body, url)
    if platform == "tripadvisor":
        return extract_tripadvisor(body, url)
    if platform == "yanolja":
        return extract_yanolja(body, url)
    if platform == "leboncoin":
        return extract_leboncoin(body, url)
    raise ValueError(f"no extractor for {platform!r}")


def _verdict(platform: str, score: ContractScore | None, error: str | None) -> str:
    """PASS / PASS w/ documented gaps / FAIL.

    Stance: missing fields that the parser documents as intentionally
    empty in logged-out HTML are NOT failures. They're honest reflections
    of platform reality. FAIL is reserved for:
      - extractor exceptions
      - missing fields the parser DOES claim to populate
      - listing_id missing (parser bug -- URL parsing should be deterministic)
    """
    if error and "extractor:" in (error or ""):
        return "FAIL (extractor error)"
    if score is None:
        return "FAIL (no body)"
    docs = _DOCUMENTED_GAPS.get(platform, set())
    real_misses = [k for k in score.missing if k not in docs]
    if not real_misses and not score.partial:
        return "PASS"
    if not real_misses:
        return "PASS w/ documented gaps"
    return f"FAIL (unexpected missing: {','.join(real_misses)})"


def _print_table(results: list[ProbeResult]) -> None:
    print("\n\n========== STEP D REPORT ==========\n")
    print("| Platform | Tier used | Status | Body size | Coverage (sat/par/mis) | Verdict |")
    print("|---|---|---|---|---|---|")
    for r in results:
        cov = r.score.coverage() if r.score else "-"
        size_kb = f"{r.body_len // 1024}KB" if r.body_len else "-"
        verdict = _verdict(r.platform, r.score, r.error)
        print(f"| {r.platform} | {r.tier_used} | {r.status} | " f"{size_kb} | {cov} | {verdict} |")


def _print_details(results: list[ProbeResult]) -> None:
    print("\n\n========== EXTRACTOR OUTPUTS ==========")
    for r in results:
        print(f"\n--- {r.platform.upper()} ---")
        print(f"  url: {r.url}")
        print(f"  tier_chain: {' -> '.join(r.fallback_chain)}")
        print(f"  status: {r.status}  body_len: {r.body_len}  elapsed: {r.elapsed_s:.1f}s")
        if r.body_path:
            print(f"  body saved: {r.body_path}")
        if r.error:
            print(f"  error: {r.error}")
        if r.score:
            print(f"  satisfied: {r.score.satisfied}")
            print(f"  partial:   {r.score.partial}")
            print(f"  missing:   {r.score.missing}")
        if r.extractor_output:
            # Print the load-bearing contract-key sources
            out = r.extractor_output
            print(f"  title           = {out.get('title')!r}")
            print(f"  listing_id      = {out.get('listing_id')!r}")
            print(f"  city            = {out.get('city')!r}")
            print(f"  country         = {out.get('country')!r}")
            print(f"  address         = {out.get('address_displayed')!r}")
            print(f"  host_name       = {out.get('host_name')!r}")
            print(f"  nightly_price   = {out.get('nightly_price')!r}")
            print(f"  price_range_txt = {out.get('price_range_text')!r}")
            print(f"  currency        = {out.get('currency')!r}")
            print(f"  photo_urls      = {len(out.get('photo_urls') or [])} urls")
            print(f"  review_count    = {out.get('review_count')!r}")
            print(f"  review_rating   = {out.get('review_rating')!r}")
            print(f"  gps_source      = {out.get('gps_source')!r}")
            print(f"  extraction_tier = {out.get('extraction_tier')!r}")
            print(f"  raw_jsonld_cnt  = {out.get('raw_jsonld_count')!r}")


def main() -> None:
    results: list[ProbeResult] = []
    for i, probe in enumerate(_PROBES):
        if i > 0:
            # Inter-platform jitter to be polite to the IP space.
            pause = random.uniform(8.0, 14.0)
            print(f"\n(sleep {pause:.1f}s before next platform)", flush=True)
            time.sleep(pause)
        try:
            r = _run_one(probe)
        except Exception as e:
            r = ProbeResult(
                platform=probe.name,
                url=probe.url,
                tier_used="",
                fallback_chain=[],
                status=0,
                body_len=0,
                elapsed_s=0.0,
                extractor_output=None,
                score=None,
                error=f"top-level: {type(e).__name__}: {e}",
                body_path=None,
            )
        results.append(r)

    _print_table(results)
    _print_details(results)


if __name__ == "__main__":
    main()
