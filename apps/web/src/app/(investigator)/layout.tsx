import type { ReactNode } from "react";
import PaletteMount from "./_palette-mount";

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
      <PaletteMount />
    </div>
  );
}
