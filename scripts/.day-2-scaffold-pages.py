"""Day -2 — Iris WI-0109 + WI-0110: scaffold the 5 case-tab placeholders + 4 top-level routes.

Writes Next.js 15 App Router files with TypeScript strict + named interfaces.
Idempotent: skips any file that already exists with non-trivial content.

Layout rationale (Iris §1):
  /                                 → redirect to /investigations
  /investigations                   → list (top-level)
  /investigations/[id]              → Dossier (default tab)
  /investigations/[id]/graph        → Cytoscape tab
  /investigations/[id]/evidence     → Evidence chain tab
  /investigations/[id]/timeline     → Timeline tab
  /investigations/[id]/export       → Export tab
  /accounts                         → SockAccount ledger (top-level peer; NOT under /settings)
  /tools                            → Tool launcher (top-level)
  /settings                         → Config (top-level)

All routes share the (investigator) layout (ThreePaneShell stub for now).
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent  # repo root
WEB = ROOT / "apps" / "web" / "src" / "app"

# Remove the .gitkeep that's blocking real content
gitkeep = WEB / ".gitkeep"
if gitkeep.exists():
    gitkeep.unlink()
    print(f"removed {gitkeep.relative_to(ROOT).as_posix()}")

files: dict[str, str] = {}

files["layout.tsx"] = '''import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "OSINT Goblin",
  description: "FOSS-first OSINT investigation dashboard",
};

export const viewport: Viewport = {
  themeColor: "#0a0a0a",
};

interface RootLayoutProps {
  children: ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          background: "#0a0a0a",
          color: "#e5e5e5",
        }}
      >
        {children}
      </body>
    </html>
  );
}
'''

files["page.tsx"] = '''import { redirect } from "next/navigation";

export default function RootPage(): never {
  redirect("/investigations");
}
'''

files["(investigator)/layout.tsx"] = '''import type { ReactNode } from "react";

/**
 * ThreePaneShell stub. Will be replaced in WI-0206 (Day 9) with the full
 * OPSEC HUD (6 tiles) + LeftRail (5 nav targets) + center surface +
 * RightRail (state + embedded CAPTCHA) + FooterPinBar.
 *
 * For now: simple top-bar + nav rail + content slot, to validate routing.
 */
interface InvestigatorLayoutProps {
  children: ReactNode;
}

const NAV_ITEMS: ReadonlyArray<{ readonly href: string; readonly label: string }> = [
  { href: "/investigations", label: "Investigations" },
  { href: "/accounts", label: "Accounts" }, // top-level peer per Iris §1; NOT under Settings
  { href: "/tools", label: "Tools" },
  { href: "/settings", label: "Settings" },
];

export default function InvestigatorLayout({ children }: InvestigatorLayoutProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      {/* OPSEC HUD stub — 6 tiles land in WI-0115 */}
      <header
        style={{
          height: 48,
          borderBottom: "1px solid #1f1f1f",
          padding: "0 16px",
          display: "flex",
          alignItems: "center",
          gap: 12,
          color: "#a3a3a3",
          fontSize: 13,
        }}
      >
        <span style={{ color: "#e5e5e5", fontWeight: 600 }}>OSINT GOBLIN</span>
        <span style={{ marginLeft: "auto" }}>OPSEC HUD — stub (WI-0115)</span>
      </header>

      <div style={{ display: "flex", flex: 1 }}>
        {/* LeftRail — top-level nav, WI-0110 */}
        <nav
          style={{
            width: 200,
            borderRight: "1px solid #1f1f1f",
            padding: 12,
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          {NAV_ITEMS.map((item) => (
            <a
              key={item.href}
              href={item.href}
              style={{
                color: "#d4d4d4",
                textDecoration: "none",
                padding: "6px 10px",
                borderRadius: 4,
                fontSize: 14,
              }}
            >
              {item.label}
            </a>
          ))}
        </nav>

        <main style={{ flex: 1, padding: 24 }}>{children}</main>

        {/* RightRail — state + embedded CAPTCHA lands in WI-0115 + WI-0111 */}
        <aside
          style={{
            width: 280,
            borderLeft: "1px solid #1f1f1f",
            padding: 16,
            color: "#737373",
            fontSize: 12,
          }}
        >
          RightRail — stub
        </aside>
      </div>

      {/* FooterPinBar — case-pinned metadata, WI-0115 */}
      <footer
        style={{
          height: 32,
          borderTop: "1px solid #1f1f1f",
          padding: "0 16px",
          display: "flex",
          alignItems: "center",
          color: "#525252",
          fontSize: 11,
        }}
      >
        v2026.05.0 · pre-Sprint-1 scaffold
      </footer>
    </div>
  );
}
'''

# Top-level routes (Iris §1)
files["(investigator)/investigations/page.tsx"] = '''import Link from "next/link";

interface InvestigationStub {
  readonly id: string;
  readonly subject: string;
  readonly opened: string;
}

const STUB_LIST: ReadonlyArray<InvestigationStub> = [
  { id: "demo-001", subject: "Username: linustorvalds", opened: "2026-05-10" },
];

export default function InvestigationsPage() {
  return (
    <section>
      <h1 style={{ fontSize: 20, margin: 0, marginBottom: 16 }}>Investigations</h1>
      <p style={{ color: "#a3a3a3", fontSize: 14, maxWidth: 640 }}>
        List + filter of investigations. Real implementation lands in Sprint 2 (WI-0206).
      </p>
      <ul style={{ marginTop: 24, padding: 0, listStyle: "none" }}>
        {STUB_LIST.map((inv) => (
          <li
            key={inv.id}
            style={{
              padding: 12,
              border: "1px solid #1f1f1f",
              borderRadius: 6,
              marginBottom: 8,
            }}
          >
            <Link
              href={`/investigations/${inv.id}`}
              style={{ color: "#e5e5e5", textDecoration: "none" }}
            >
              <strong>{inv.subject}</strong>
              <span style={{ color: "#737373", marginLeft: 12, fontSize: 12 }}>
                {inv.id} · opened {inv.opened}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
'''

# Case-tab container layout (WI-0109)
files["(investigator)/investigations/[id]/layout.tsx"] = '''import type { ReactNode } from "react";

interface CaseLayoutProps {
  children: ReactNode;
  params: Promise<{ id: string }>;
}

const TABS: ReadonlyArray<{ readonly slug: string; readonly label: string }> = [
  { slug: "", label: "Dossier" }, // default — / route
  { slug: "graph", label: "Graph" },
  { slug: "evidence", label: "Evidence" },
  { slug: "timeline", label: "Timeline" },
  { slug: "export", label: "Export" },
];

export default async function CaseLayout({ children, params }: CaseLayoutProps) {
  const { id } = await params;
  return (
    <div>
      {/* Iris §1 — facet rail above dossier; placeholder for now */}
      <div
        style={{
          padding: "8px 12px",
          background: "#141414",
          border: "1px solid #1f1f1f",
          borderRadius: 6,
          marginBottom: 16,
          color: "#737373",
          fontSize: 12,
        }}
      >
        Facet rail (Aleph-inspired) — Dataset · Dates · Entity type · Country · Email · Phone · Name · Address · Configure filters
      </div>

      {/* Tabs row */}
      <nav
        style={{
          display: "flex",
          gap: 4,
          borderBottom: "1px solid #1f1f1f",
          marginBottom: 16,
        }}
      >
        {TABS.map((tab) => {
          const href = tab.slug ? `/investigations/${id}/${tab.slug}` : `/investigations/${id}`;
          return (
            <a
              key={tab.slug || "dossier"}
              href={href}
              style={{
                padding: "8px 14px",
                color: "#d4d4d4",
                textDecoration: "none",
                fontSize: 13,
                borderBottom: "2px solid transparent",
              }}
            >
              {tab.label}
            </a>
          );
        })}
      </nav>

      <div>{children}</div>
    </div>
  );
}
'''

# 5 case tabs (Iris WI-0109)
def tab_page(title: str, body_hint: str, wi_ref: str) -> str:
    return f'''interface PageProps {{
  params: Promise<{{ id: string }}>;
}}

export default async function {title.replace(' ', '')}Page({{ params }}: PageProps) {{
  const {{ id }} = await params;
  return (
    <section>
      <h2 style={{{{ fontSize: 16, margin: 0, marginBottom: 8 }}}}>{title}</h2>
      <p style={{{{ color: "#737373", fontSize: 13 }}}}>
        Case <code style={{{{ color: "#a3a3a3" }}}}>{{id}}</code> · {body_hint}
      </p>
      <p style={{{{ color: "#525252", fontSize: 12, marginTop: 16 }}}}>
        Real implementation lands in {wi_ref}.
      </p>
    </section>
  );
}}
'''

files["(investigator)/investigations/[id]/page.tsx"] = tab_page(
    "Dossier", "DossierSection-as-spine (default tab)", "WI-0206 (Sprint 2)"
)
files["(investigator)/investigations/[id]/graph/page.tsx"] = tab_page(
    "Graph", "Cytoscape.js entity graph (lazy-mounted)", "WI-0206 + WI-0112 (Sprint 2)"
)
files["(investigator)/investigations/[id]/evidence/page.tsx"] = tab_page(
    "Evidence", "Forensic chain + signed artifacts", "WI-0202 + WI-0205 (Sprint 2)"
)
files["(investigator)/investigations/[id]/timeline/page.tsx"] = tab_page(
    "Timeline", "Event timeline of captures + pivots", "WI-0206 (Sprint 2)"
)
files["(investigator)/investigations/[id]/export/page.tsx"] = tab_page(
    "Export", "Evidence-package zip (WARC + sigs + manifest + verify.py)", "WI-0202 + WI-0208 (Sprint 1 exit gate)"
)

# Top-level routes WI-0110
files["(investigator)/accounts/page.tsx"] = '''export default function AccountsPage() {
  return (
    <section>
      <h1 style={{ fontSize: 20, margin: 0, marginBottom: 8 }}>Accounts</h1>
      <p style={{ color: "#a3a3a3", fontSize: 14, maxWidth: 640 }}>
        Sock-account ledger. <strong>Top-level peer of Investigations</strong> — not nested under
        Settings (per Iris §1 + Diego ADR-0010 structural-isolation rule).
      </p>
      <p style={{ color: "#737373", fontSize: 13, marginTop: 16 }}>
        Sock accounts live in a separate Postgres database
        (<code style={{ color: "#a3a3a3" }}>osint_sockaccounts</code>) with physical import-path
        split and CI lint rejecting cross-imports. UI lands in WI-0708 (Sprint 7).
      </p>
    </section>
  );
}
'''

files["(investigator)/tools/page.tsx"] = '''export default function ToolsPage() {
  return (
    <section>
      <h1 style={{ fontSize: 20, margin: 0, marginBottom: 8 }}>Tools</h1>
      <p style={{ color: "#a3a3a3", fontSize: 14, maxWidth: 640 }}>
        Curated tool launcher: Maigret, Sherlock, Holehe-successor, GHunt (subprocess), Amass,
        bbot (subprocess), and the 12-adapter M1 set.
      </p>
      <p style={{ color: "#737373", fontSize: 13, marginTop: 16 }}>
        Real launcher lands in WI-0206 (Sprint 2). Adapter registry in WI-0204 (Sprint 1).
      </p>
    </section>
  );
}
'''

files["(investigator)/settings/page.tsx"] = '''export default function SettingsPage() {
  return (
    <section>
      <h1 style={{ fontSize: 20, margin: 0, marginBottom: 8 }}>Settings</h1>
      <p style={{ color: "#a3a3a3", fontSize: 14, maxWidth: 640 }}>
        Configuration: OPSEC profile, Tor circuit policy, rate-limit budgets, lawful-basis
        defaults, retention policy.
      </p>
      <p style={{ color: "#737373", fontSize: 13, marginTop: 16 }}>
        Real settings UI lands in WI-0708 (Sprint 7) alongside OPSEC profile manager.
      </p>
    </section>
  );
}
'''

# Write all
written = 0
skipped = 0
for rel, content in files.items():
    p = WEB / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and len(p.read_text(encoding="utf-8")) > len(content) // 2:
        # Already has content — overwrite only if our content is more substantial
        # Special case: replace the trivial stub page.tsx
        existing = p.read_text(encoding="utf-8")
        if "OSINT Goblin" in existing and len(existing) < 100:
            # this is the trivial stub, replace
            pass
        else:
            print(f"skip {rel} (already has content)")
            skipped += 1
            continue
    p.write_text(content, encoding="utf-8")
    print(f"wrote {rel}")
    written += 1

# Remove any leftover .gitkeep in the new dirs
for gk in (WEB / "(investigator)").rglob(".gitkeep"):
    gk.unlink()
    print(f"removed {gk.relative_to(ROOT).as_posix()}")

print(f"\ntotal: {written} written, {skipped} skipped")
print("next: pnpm install at repo root, then pnpm --filter @osint-goblin/web dev")
