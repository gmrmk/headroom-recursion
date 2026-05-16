# tasks/todos.md

## ACTIVE: Sprint — Stabilize + Verify (resumed 2026-05-16)

**Trigger:** Last session shipped ~3000 LOC across 8 modified + 20+ untracked files
without intermediate commits. Memory snapshot of "424 pass, 1 skip" describes work
that lives only on disk, not in git. Plus documented Ship-10 verify gap: the 3 new
parsers (TripAdvisor / Yanolja / Leboncoin) were verified against synthetic fixtures
matching documented schema shapes, but **not** pressure-tested against live response
bodies. Per the `2026-05-16 — Verify before marking complete` lesson, that's the gap.

**Goal:** Bring git in sync with reality. Then prove the 3 new parsers work on real
bodies. Then move on with a clean tree.

### Steps

#### A. Baseline confirm
- [ ] Run `apps/workers` test suite on dirty tree. Confirm the documented 424 pass / 1 skip.
      If anything red, stop and re-plan. No commits until baseline is green.

#### B. Commit triage (subagent dispatch)
- [ ] Dispatch Explore subagent to map the uncommitted hoard into reviewable commit
      groupings. Required output: ordered list of `{subject, files[], 1-line reasoning}`,
      6–9 groups, conventional-commits subjects.
- [ ] User checkpoint: review the proposed groupings before any commits land.

#### C. Execute commit chain
- [ ] Commit each group sequentially (foundational deps first). After every commit,
      re-run pytest to confirm the green stays green.
- [ ] Anticipated order (subject to subagent proposal):
      1. `feat(privacy): ephemeral.py + Naomi-strict adapter refactor` (+ ADRs 0027–0029)
      2. `feat(opsec): zendriver tier + manual cookie injection in humanize`
      3. `feat(image): consolidated photo-match-cluster + listing-match-summary events`
      4. `feat(listing): 170-platform vendor coverage expansion`
      5. `feat(listing): TripAdvisor + Yanolja + Leboncoin bespoke parsers`
      6. `docs(adr): adapter-package-location + risk-score-scope + fingerprint-scope + pivot-doctrine`
      7. `chore(dev): listing/tier/bypass pressure-test scripts + captured probe bodies`
      8. `feat(web): playwright e2e config + investigation-flow spec` (if independent of other web work)
      9. `chore(packages): sub-brand-catalog skeleton` (depending on contents)
      10. `docs(tasks): session 2026-05-16 progress + lessons captured`

#### D. Live pressure-test the 3 new parsers (subagent + existing script)
- [ ] Extend or mirror `tools/dev/pressure-test-listing.py` to cover TripAdvisor,
      Yanolja, Leboncoin. Use the humanize tier each platform's vendor maps to
      (`zendriver` for TA per DataDome routing; `patchright` for Yanolja + Leboncoin).
- [ ] For each platform: capture real body to `tools/dev/listing-failure-bodies/`,
      run its bespoke parser against the captured body, record contract-key coverage
      (title / price / location / photos / host / jurisdiction / id).
- [ ] Report: matrix of platform × contract-key with pass/fail/partial.

#### E. Iterate parsers if real bodies expose contract gaps
- [ ] For each gap: distinguish schema drift (the real body's shape differs from
      our synthetic fixture) vs parser bug. Patch the parser, pin the real body
      as a regression fixture, add a test that fails before / passes after.

#### F. Commit verification artifacts
- [ ] One commit: `test(listing): live-body fixtures + regression tests for TA / Yanolja / Leboncoin`.

#### G. Close
- [ ] Update this todos.md (move Stabilize + Verify into Completed; promote a Pending
      item into the next ACTIVE sprint).
- [ ] Update mempalace memory with verified parser state.
- [ ] Update the `Known gaps deferred` section: remove the TA/Yanolja/Leboncoin gap
      since it's now closed.

### Definition of done

- Working tree clean (incidentals like `tsconfig.tsbuildinfo` and graphify caches OK).
- 3 new parsers run against real response bodies and produce the cross-platform
  contract keys.
- `Known gaps deferred` no longer mentions the TA/Yanolja/Leboncoin pressure-test gap.
- mempalace memory refreshed.

### Risk audit

| Risk | Mitigation |
|---|---|
| Live fetch hits 429 due to prior recon IP-flagging | Cookie injection fallback path is documented; if hit, surface to user, don't burn proxy budget guessing |
| Real body exposes schema drift on all 3 parsers simultaneously | Patch one at a time, pin each real body as fixture; don't try to make one giant generic fix |
| Test failure mid-commit-chain because of cross-file coupling | Run pytest after each commit; if red, stop and split the offending commit further |
| Subagent commit-grouping proposal misclassifies a file | User checkpoint after subagent returns; adjust before execution |

---

## QUEUED NEXT: Sprint — IP Intel + Triangulation Widget

**Status:** plan only; kicks off after the Stabilize + Verify sprint closes.
**Trigger:** user 2026-05-16: "I want to find out if theyre hiding behind a VPN
and get a map pin of their IP data, for free" + clarification "I wanna borrow it
for observation, triangulation, typical you-are-who-you-say-you-are OSINT stuff"
+ "once I close the dossier, poof its gone, I dont even want to see it, its
temporary" + "interlacing Max Mind, IP2Proxy, TOR, and X4Bnet into their own
interactive widget on the dossier" + "I want to treat the dossier like its a
beautifully arranged HTML popout artifact".

### Vision

The investigator pastes target IPs (or pastes raw email headers and the system
extracts `Received: from <IP>`). The dossier renders a beautifully arranged
HTML popout artifact: one `IPIntelCard` per IP with a Leaflet map pin, plus a
sibling `TriangulationCard` that plots every IP in the investigation on one
map with distance rulers and a self-consistency score. Closes the
"you-are-who-you-say-you-are" loop.

Shred on dossier close. IPs never persist to disk in any form. Reference
databases (knowledge bases, not target data) live under `data/reference/` and
are gitignored; downloaded on first run via `make ip-refdata`.

### Signal stack (5 sources, all free)

| Source | License | Signal | Storage |
|---|---|---|---|
| MaxMind GeoLite2 City + ASN | Free w/ account | geo + ASN org | `data/reference/GeoLite2-City.mmdb` (~70MB local) |
| IP2Proxy LITE PX11 | Free w/ attribution | is_vpn / is_tor / is_residential / is_corporate / is_datacenter / is_mobile | `data/reference/IP2PROXY-LITE-PX11.BIN` (~50MB local) |
| Tor Project bulk-exit-list | Public | definitive Tor flag | `data/reference/tor-exit.txt` (refresh daily; ~50KB) |
| X4BNet/lists_vpn | MIT | community-curated VPN + datacenter CIDR ranges | `data/reference/x4bnet-vpn.txt` (refresh weekly) |
| ASN heuristic | n/a | residential confidence from AS class | derived in-process |

### Consensus aggregator

Single `IPVerdict` object stacks all 5 sources into one verdict with
`source_attestations` for the per-source breakdown in the popout. The
`consensus_strength` drives widget color: high when sources agree, low when
split (investigator review prompt).

### Plan

#### Phase 1 — Reference-database bootstrap
- `infra/scripts/fetch-ip-refdata.{sh,ps1}` — downloads + verifies the 4
  reference databases into `data/reference/`. Idempotent. `make ip-refdata`
  invokes it. Documents the MaxMind account requirement in README.
- `data/reference/` added to `.gitignore`.
- ADR: `0030-ip-intel-reference-databases.md` documenting license terms,
  attribution requirements, refresh cadence.

#### Phase 2 — Backend adapters (Naomi-strict)
- `apps/workers/src/osint_goblin_workers/adapters_ip.py`:
  - `ip_intel_lookup(payload={ip}|{ips})` → emits one `ip-intel` event per IP
    carrying the full `IPVerdict` object. In-memory only.
  - `email_header_parse(payload={raw_headers})` → extracts all
    `Received: from <IP>` lines and emits `extracted-ip` events feeding
    `ip_intel_lookup`. Never persists the raw header.
  - 5 lookup helpers (`_maxmind_lookup`, `_ip2proxy_lookup`,
    `_tor_exit_lookup`, `_x4bnet_lookup`, `_asn_heuristic`).
  - Consensus aggregator (`_compute_verdict`) layering source attestations.
- Wires into `_REGISTRY` as `in_process=True`. Workflow id `w7.ip`.
- ~25 unit tests pinning the consensus algorithm + Naomi-strict invariants
  (no-disk-write, shred-on-close, IP-not-in-paths).

#### Phase 3 — Form input
- Extend `InvestigationForm` (apps/web/src/components/investigation-form.tsx):
  - Add IP field (optional, accepts comma-separated)
  - Add "Email headers" textarea (paste-in; auto-extracts IPs server-side
    and never echoes the raw text back to the dossier)
- Routing in `workflow-routing.ts`: IP field OR email-headers field → `w7.ip`

#### Phase 4 — IPIntelCard component
- `apps/web/src/components/ip-intel-card.tsx`:
  - Header: IP + provenance badge ("from Received: header" / "DNS lookup")
  - Leaflet map (react-leaflet + OSM tiles, free with attribution)
  - ASN row + reverse-DNS
  - 4 verdict pills (Tor / VPN / Datacenter / Residential confidence)
  - Consensus-strength bar with "Show attestations" disclosure
- Pure functions only; consumes `IPVerdict` from the event stream

#### Phase 5 — TriangulationCard component
- `apps/web/src/components/triangulation-card.tsx`:
  - Activates when 2+ IPs are in the dossier
  - One Leaflet map with all pins + distance rulers between them
  - Self-consistency score (claimed vs captured locations)
  - Provenance row: which IP came from what source
  - Investigator-annotated "claimed location" input (in-memory only)

#### Phase 6 — Static export for HTML popout artifact
- Extend `lib/dossier-shape.ts` to project `ip-intel` + `triangulation` events
  into Sections.
- Extend `serializeDossierHtml`:
  - Render `IPIntelCard` and `TriangulationCard` as static HTML with embedded
    SVG maps (via `staticmaps` or pre-rendered Leaflet snapshots inline-base64).
  - Offline-viewable; no external requests on open.
- Investigator gets a single .html file with the full triangulation analysis.

#### Phase 7 — Naomi-strict CI guards + shred-on-close hardening
- Static-grep CI test: assert no IP-like patterns leak into any
  persisted location (data/*, tasks/*, logs).
- `_CtxState.shred()` extended to drop `ip_lookups` dict.
- `IPVerdict` excluded from anything that hits disk; only the rendered
  Section makes it to the dossier export.

#### Phase 8 — Live verification
- Test against 3 known patterns: known-Tor exit, known-AWS-IP, known-residential.
- Verify consensus_strength behaves correctly when sources disagree.
- Mock email-header paste with synthetic `Received:` chain.

### Definition of done

- The 4 reference databases download + load cleanly.
- `ip_intel_lookup` returns IPVerdict for any IPv4 input with consensus across
  all 5 sources.
- `email_header_parse` extracts every Received-line IP and never echoes the
  raw header.
- IPIntelCard renders premium per IntelBase aesthetic (matches the existing
  design tokens).
- TriangulationCard appears when 2+ IPs, with a working map + ruler.
- `.html` export is fully offline-viewable with inline static maps.
- Shred-on-close drops every IP from memory; nothing persists.
- CI guard catches any future code that tries to log/persist an IP.

### Risk audit

| Risk | Mitigation |
|---|---|
| MaxMind requires an account (small friction) | Document in README + `make ip-refdata` instructs the user once |
| IP2Proxy LITE has a per-IP query limit on free CSV | LITE BIN file is unlimited; bundle the BIN flavor |
| Leaflet/OSM tile fetches during dossier open could leak intent | Static-export inlines map SVG; UI mode optionally proxies tiles through the worker |
| Source disagreement (e.g. one says VPN, others say residential) | `consensus_strength` surfaces the split rather than forcing one answer |
| Investigator pastes email headers from a victim, not target | Document the consent model; never assume the paster is the source |
| Reference databases become stale | Refresh cadence in `make ip-refdata`; staleness warning in card if last refresh > 30 days |

---

## PARKED: Dashboard redesign (IntelBase-style)

**Status:** Phase 1–6 mostly shipped (commits 8600830 → 8dc11f8). Remaining UI work
moved to the Pending list below (Ship 10-UI intake form + Ship 10-UI dossier output).

**Original trigger:** user "I should just be able to type in the information when prompted in fields, and then it goes and stitches everything together for me" + "rich one-glance report with links" + "premium UI like IntelBase"

---

## Vision

Investigator opens the dashboard, types into semantic fields (Address, Email, Phone, Owner, Photo URL, Username, IP, Domain), clicks **Investigate**, and sees a single rich report appear and update live below: verdict at the top, findings grouped by category (Identity / Behavior / Compromise / Property / Visual), every finding clickable. Image evidence inline.

The current event-stream + adapter-dropdown surface moves behind a "Power user" link — preserved, not deleted.

## Architectural insight

The HTML dossier serializer already does the grouping + categorization + inline-image work the new report needs. Lift that logic into pure `lib/dossier-shape.ts`. Two consumers: (a) the existing HTML serializer (string output → `.html` download), (b) the new React `InvestigationReport` (live DOM, updates as events arrive). One source of truth — both stay in sync by construction.

---

## Phase 1 — design tokens + 4 primitives

**Files:**
- `apps/web/src/app/styles/tokens.css` — OKLCH palette, fluid type, spacing scale, radii, shadows, ease/duration
- `apps/web/src/components/ui/Card.tsx`
- `apps/web/src/components/ui/Button.tsx`
- `apps/web/src/components/ui/Stack.tsx`
- `apps/web/src/components/ui/MetaText.tsx`

**Definition of done:** `tokens.css` imported in `app/layout.tsx`; primitives render with zero `style={{...}}` literals; tsc + next build clean.

## Phase 2 — `lib/dossier-shape.ts`

Pure module exporting:
- `groupFindings(events): Section[]` where Section = { id, title, source_kind, items: Finding[], image_evidence: ... }
- `Finding` shape: `{ label, source_url?, samples?, raw_payload? }`
- Project the existing event types into 5 reader-friendly sections:
  - **Identity** — gravatar / github_commits person-matches; verified accounts
  - **Behavior** — github commits stats; user_scanner platforms
  - **Compromise** — hudson_rock breach-hits; hibp breaches
  - **Property** — geocode + nearby_features + listings
  - **Visual** — image-match events with `*_rel` paths
- Reuse: `serializeDossierHtml` consumes `groupFindings` to build its body. No behavior change to the existing HTML export.

**Definition of done:** existing 35/35 HTML smoke still green; new module covered by ~10 unit tests.

## Phase 3 — `InvestigationForm`

`apps/web/src/components/investigation-form.tsx`:
- Fields: Address, Email, Phone, Owner, Photo URL, Username, IP, Domain, Notes (free text)
- Each field is optional; investigator fills what they have
- "What this will run" preview block below the fields — explicit list of workflow IDs that will dispatch given current inputs
- Single primary **Investigate** button (variant=primary)
- "Run individual adapter" link → opens existing RunToolForm in a collapsible panel (dev/power-user)

`apps/web/src/lib/workflow-routing.ts` — pure deterministic routing:
```
filled fields → list of workflow ids
  address ∩ host_name ∩ photo_url → ["w9.pv"]
  email → ["w11.em"]
  ip → ["w10.ip"]
  phone → ["w3.ph"]
  domain → ["w5.do"]
  username → ["w1.un"]
```
Multiple may dispatch in parallel. Covered by unit tests.

**Definition of done:** form renders premium; routing rules unit-tested; preview block updates live as fields change.

## Phase 4 — `InvestigationReport`

`apps/web/src/components/investigation-report.tsx`:
- Verdict card at top (reuse the VerdictBanner pattern but more prominent)
- Section cards for Identity / Behavior / Compromise / Property / Visual
- Each finding rendered with: label, source link (opens in new tab), sample data, redacted-credential placeholders where present
- Image evidence: inline thumbnails from `*_rel` paths via `/api/files/<rel>`
- "Save dossier" buttons at top-right (.md and .html, reuse existing exporters)
- Empty sections hidden (don't show "Visual: 0 findings")

**Definition of done:** report renders live as events stream in via the existing SSE; uses the dossier-shape projection; visually unified with the InvestigationForm via the design tokens.

## Phase 5 — Page recompose

`apps/web/src/app/(investigator)/investigations/[id]/page.tsx`:
- Top: `<InvestigationForm investigationId={id} />`
- Below: `<InvestigationReport investigationId={id} />`
- Hidden behind a "Show live activity" toggle: the current `<EventStream investigationId={id} />`

**Definition of done:** page renders premium; the old surface remains reachable behind the toggle.

## Phase 6 — Cutover

- `RunToolForm` → moves behind "Power user" link inside InvestigationForm
- Hidden by default
- No code deleted; everything still works

**Definition of done:** parity-or-better with prior functionality; tsc + next build + all smokes green; existing tests pass.

---

## Risk audit

| Risk | Mitigation |
|---|---|
| Tokens churn breaks existing components | Phase 1 only adds; doesn't migrate. Migration happens in Phase 5 |
| Workflow routing dispatches wrong workflow | Show "What this will run" preview before click; transparent rule |
| Live React report and HTML serializer drift | Share `dossier-shape.ts` as the single source of truth |
| Cutover hides bugs | Keep old surface reachable behind toggle |
| ~6-7h total | Ship in 5-6 commits, each independently working |

---

## Out of scope

- Server-side rendering of the report (it's live React; SSR-rendered report would require a different SSE strategy)
- Authentication / multi-user (this is a personal-use tool)
- Workflow chaining beyond the existing `inputs_from` (no DAG editor)
- Theme switching (dark mode only for v1; light mode can come later)

---

# Current sprint snapshot (2026-05-16)

Mirroring live TodoWrite state into the canonical artifact per CLAUDE.md
discipline. This sprint is a continuation of Ship 8 / Ship 10 work.

## Completed this session

- [x] Ship 4 closed: 10 international search engines
- [x] Ship 10 scaffold: Airbnb extractor + universal owner-mention scanner
- [x] Ship 10-Booking parser
- [x] Ship 8: humanize.py 8-layer OPSEC + 4 browser tiers
- [x] Camoufox tier MS-Store-Python sandbox fix (realpath of executable_path + UBO addon)
- [x] Pressure-test matrix + Imperva root-cause analysis
- [x] Bypass-stack probe → **Zendriver defeats Imperva PWA + DataDome**
- [x] Zendriver added as Tier-5 in humanize.py (6 contract tests)
- [x] Manual-cookie-injection across Playwright + zendriver tiers (9 tests)
- [x] Ship 10-VRBO bespoke parser (22 tests)
- [x] Geographic expansion: **242 hostnames → 170 platform-ids**, 0 vendor-map drift. Markets: CN, DE/DACH, ES, PT, FR, IT, EU, RU, ZA, KE, NG, SA, IN, LK, PH, ID, VN, BR, MX, PA, UK, IE, CL, CA, Nordic, NL, CH, AT, BE
- [x] E2E image-pipeline test on real VRBO photo (8 adapters)
- [x] Installed pdqhash C-extension; flipped `reverse_image_aggregator` to live mode (was hardcoded synthetic)
- [x] `listing_photo_pivot` recursive photo fan-out (Ship 7 photo-fraud variant)
- [x] **Ephemeral mode (Naomi-strict)**: built `ephemeral.py`, refactored 3 disk-write sites in adapters_image, 17 invariant tests, purged 600KB of leftover landlord photos from `data/`
- [x] Consolidated photo-match output: `listing-match-summary` per visited listing + terminal `photo-match-cluster` event (7 contract tests)
- [x] TripAdvisor + Yanolja + Leboncoin bespoke parsers (verified against representative fixtures; cross-platform key contract satisfied for all 3)

**Test state: 424 pass, 1 skip.** Up from 24 at start of Ship 8.

## Pending

- [ ] **Ship 10-UI dossier intake**: checkbox-driven progressive-disclosure form (URL / Host / Phone / Email / Photo / Cookie sections) + backend filter that dispatches only populated adapters
- [ ] **Ship 10-UI dossier output**: render `listing-match-summary` tables + `photo-match-cluster` graph + GPS map widget
- [ ] Ship 8 follow-ups: logless CI test + ephemeral-default audit + shred-after-view UI button
- [ ] Ship 11-Social platforms (WhatsApp/WeChat/Telegram/KakaoTalk/LINE/Instagram/FB/Reddit/Bluesky/Mastodon/TikTok/Pinterest)
- [ ] Ship 5: identity stack (HudsonRock + HIBP + IntelX + LinkedIn + TrueCaller + EmailRep)
- [ ] Ship 6: Public records + court (CourtListener/RECAP/NSOPW/county-assessor/Zillow)
- [ ] Ship 7: rest of pivot loops (entity, address, phone, email)
- [ ] Ship 9: Captcha + paywall defeat

## Review section (CLAUDE.md task-management step 5)

**Major architectural shifts this session:**

1. **Zendriver is now the primary tier for hardest anti-bot vendors.** Pressure-tested 2026-05-16 against VRBO/Expedia Imperva PWA challenge (where every Playwright tier failed) and TripAdvisor DataDome — both returned real 200 OK + real content. The vendor map routes `imperva` and `datadome` to zendriver as a result.

2. **Ephemeral mode is structurally default.** This is a Naomi-gate hardening past what Ship 8 had. The repo's `data/` directory is no longer a write target for any normal investigator flow. Three disk-write sites refactored. In-process `_PhashMemoryStore` replaces the JSONL audit trail. Investigation IDs are routing keys only — never persisted, never logged, never in artifact paths.

3. **Consolidated photo-match output gives the dossier UI a single object to render.** `photo-match-cluster` terminal event aggregates every cross-listing photo hit ranked by host diversity (`cross-platform-duplicate` verdict fires when ≥2 distinct hosts). Per-listing `listing-match-summary` events surface the link list for each visited listing.

4. **170-platform coverage means most photo-fraud lookups hit a known platform.** Suspect Airbnb listing's photo turns up on a Spanish idealista, a Brazilian quintoandar, a Korean yanolja — all detected and surfaced via the same pipeline.

**Known gaps deferred:**

- Reverse-image engines (Yandex/Lens/TinEye/Bing) sometimes return 0 matches because the engines block scrapers; could route them through humanize.py tier ladder + manual cookies. Deferred until first false-negative is observed in real PV work.
- Sightengine cloud AI-detection requires an API key; graceful error message guides setup. Optional.
- Botasaurus + NoDriver require a separate Chrome install; deprioritized since Zendriver auto-located a working Chromium.
- TripAdvisor / Yanolja / Leboncoin parsers verified against synthetic fixtures matching the documented schema.org/Next.js shapes — but NOT yet pressure-tested against live response bodies for those three platforms. Schedule a probe run before declaring them production-grade.
