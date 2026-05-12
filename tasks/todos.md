# tasks/todos.md — Dashboard redesign (IntelBase-style)

**Status:** in-flight
**Created:** 2026-05-11 (replaces output-mapping plan, now shipped)
**Trigger:** user "I should just be able to type in the information when prompted in fields, and then it goes and stitches everything together for me" + "rich one-glance report with links" + "premium UI like IntelBase"

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
