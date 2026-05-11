"""Day -1 — WI-0111 + WI-0113: cmd-K placeholder + keyboard handler + attest route.

Per Hideo §3 + Iris §10.2-D + Camille §5.1.1:
  - cmd-K opens an empty CommandDialog (stub for shadcn/cmdk integration in WI-0606)
  - cmd-Shift-K reserved for scoped palette (separate binding, not auto-detect)
  - Verb letters (p/c/a/o/e) are not yet bound — Hideo's spec lives in ADR-0011 (Sprint 1 WI-0114)
  - Esc dismisses
  - Keyboard handler centralized at apps/web/src/lib/keyboard.ts
  - /investigations/[id]/attest?reason=initial|reaffirm|expired renders placeholder Sheet
    with INTEGRATION-SPEC §8 prompt copy verbatim (frozen baseline for Camille §5.1.1)
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
WEB_SRC = ROOT / "apps" / "web" / "src"

files: dict[str, str] = {}

# Central keyboard handler — Iris §10.2 mandates centralization
files["lib/keyboard.ts"] = '''/**
 * Centralized keyboard handler — Iris §10.2 + ADR-0007 (six verbs).
 *
 * Sprint-1 scope: cmd-K root palette + cmd-Shift-K scoped + Esc dismiss.
 * Verb-letter bindings (p/c/a/o/e) land in WI-0114 (ADR-0011 commit).
 *
 * All key bindings register here. No component should ever attach a window-level
 * key listener directly — collisions become invisible at PR time.
 */

export type KeyboardScope = "global" | "investigation" | "modal";

export interface KeyBinding {
  readonly id: string;
  readonly key: string; // e.g. "k", "shift+k", "Escape"
  readonly mod: "cmd" | "ctrl" | "cmd-shift" | "ctrl-shift" | "alt" | null;
  readonly scope: KeyboardScope;
  readonly description: string;
}

/**
 * The canonical binding table. WI-0114 will extend this with the verb letters
 * after ADR-0011 lands.
 */
export const KEY_BINDINGS: ReadonlyArray<KeyBinding> = [
  {
    id: "palette.root",
    key: "k",
    mod: "cmd",
    scope: "global",
    description: "Open root command palette",
  },
  {
    id: "palette.scoped",
    key: "k",
    mod: "cmd-shift",
    scope: "investigation",
    description: "Open scoped command palette (current investigation only)",
  },
  {
    id: "modal.dismiss",
    key: "Escape",
    mod: null,
    scope: "modal",
    description: "Dismiss top-most modal / palette",
  },
];

/** Detect cmd (macOS) vs ctrl (Win/Linux) so bindings work cross-platform. */
export function matchesMod(event: KeyboardEvent, mod: KeyBinding["mod"]): boolean {
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  const primary = isMac ? event.metaKey : event.ctrlKey;

  switch (mod) {
    case null:
      return !primary && !event.shiftKey && !event.altKey;
    case "cmd":
    case "ctrl":
      return primary && !event.shiftKey && !event.altKey;
    case "cmd-shift":
    case "ctrl-shift":
      return primary && event.shiftKey && !event.altKey;
    case "alt":
      return event.altKey && !primary && !event.shiftKey;
    default:
      return false;
  }
}

export function matchesBinding(event: KeyboardEvent, binding: KeyBinding): boolean {
  return (
    event.key.toLowerCase() === binding.key.toLowerCase() && matchesMod(event, binding.mod)
  );
}
'''

# Command palette React component — minimal stub
files["components/command-palette.tsx"] = '''"use client";

import { useEffect, useState } from "react";
import { KEY_BINDINGS, matchesBinding } from "../lib/keyboard";

export interface CommandPaletteProps {
  /** When true, force-open (e.g. invoked from a sister component). */
  forceOpen?: boolean;
}

export function CommandPalette({ forceOpen = false }: CommandPaletteProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [scoped, setScoped] = useState(false);

  useEffect(() => {
    setIsOpen(forceOpen);
  }, [forceOpen]);

  useEffect(() => {
    function handleKey(event: KeyboardEvent) {
      const rootBinding = KEY_BINDINGS.find((b) => b.id === "palette.root");
      const scopedBinding = KEY_BINDINGS.find((b) => b.id === "palette.scoped");
      const dismissBinding = KEY_BINDINGS.find((b) => b.id === "modal.dismiss");
      if (!rootBinding || !scopedBinding || !dismissBinding) return;

      if (matchesBinding(event, rootBinding)) {
        event.preventDefault();
        setScoped(false);
        setIsOpen(true);
      } else if (matchesBinding(event, scopedBinding)) {
        event.preventDefault();
        setScoped(true);
        setIsOpen(true);
      } else if (isOpen && matchesBinding(event, dismissBinding)) {
        event.preventDefault();
        setIsOpen(false);
      }
    }

    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={scoped ? "Scoped command palette" : "Command palette"}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={() => setIsOpen(false)}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          marginTop: "15vh",
          width: 600,
          maxWidth: "90vw",
          background: "#141414",
          border: "1px solid #2a2a2a",
          borderRadius: 8,
          padding: 16,
          color: "#e5e5e5",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
          <span style={{ color: "#737373", fontSize: 12 }}>
            {scoped ? "Scoped (current investigation)" : "Root"}
          </span>
          <span style={{ marginLeft: "auto", color: "#525252", fontSize: 11 }}>
            stub — shadcn/cmdk integration in WI-0606
          </span>
        </div>
        <input
          type="text"
          placeholder={scoped ? "Search in this investigation..." : "Type a verb or workflow..."}
          autoFocus
          style={{
            width: "100%",
            padding: 12,
            background: "#0a0a0a",
            border: "1px solid #2a2a2a",
            borderRadius: 6,
            color: "#e5e5e5",
            fontSize: 14,
            outline: "none",
            boxSizing: "border-box",
          }}
        />
        <div style={{ marginTop: 16, color: "#737373", fontSize: 12 }}>
          <p style={{ margin: 0 }}>
            Empty state will render the W1-W8 workflow grid (Hideo §3) after 0-5 palette
            actions in case; recents above after.
          </p>
        </div>
      </div>
    </div>
  );
}
'''

# Wire the CommandPalette into the (investigator) layout
# We need to update layout.tsx to mount it
files["app/(investigator)/_palette-mount.tsx"] = '''"use client";

import { CommandPalette } from "../../components/command-palette";

/** Client-component mount point for the cmd-K palette. Keeps the parent layout server-side. */
export default function PaletteMount() {
  return <CommandPalette />;
}
'''

# Attest route — Camille §5.1.1 verbatim prompt copy
files["app/(investigator)/investigations/[id]/attest/page.tsx"] = '''import type { Metadata } from "next";

interface AttestPageProps {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ reason?: "initial" | "reaffirm" | "expired" }>;
}

export const metadata: Metadata = {
  title: "Lawful Basis Attestation",
};

/**
 * Lawful-basis attestation route — Camille §5.1.1 verbatim baseline.
 * Real implementation lands in WI-0606 (Sprint 1) with bounded reaffirmation chain
 * (Tomas §10.F + Camille INV-S1: ≤20 reaffirmations OR ≤24h, whichever first).
 *
 * URL contract (Iris §10.2-D): /investigations/[id]/attest?reason=initial|reaffirm|expired
 */
export default async function AttestPage({ params, searchParams }: AttestPageProps) {
  const { id } = await params;
  const { reason = "initial" } = await searchParams;

  const reasonText: Record<NonNullable<typeof reason>, string> = {
    initial: "Initial attestation for this investigation",
    reaffirm: "Re-affirming bounded chain (≤20 matches or ≤24h since full attestation)",
    expired: "Previous attestation expired — full retype required",
  };

  return (
    <main
      style={{
        maxWidth: 720,
        margin: "10vh auto",
        padding: 32,
        background: "#0f0f0f",
        border: "1px solid #2a2a2a",
        borderRadius: 8,
        color: "#e5e5e5",
        fontFamily: "ui-monospace, monospace",
        lineHeight: 1.55,
      }}
    >
      <h1 style={{ fontSize: 18, margin: 0, marginBottom: 8 }}>
        Lawful basis attestation required.
      </h1>
      <p style={{ color: "#a3a3a3", fontSize: 13, marginTop: 0 }}>
        Case <code style={{ color: "#e5e5e5" }}>{id}</code> · {reasonText[reason]}
      </p>

      <div style={{ marginTop: 24 }}>
        <p>
          I, <strong>{`{investigator handle}`}</strong>, attest that the biometric processing
          about to occur is necessary for the investigation{" "}
          <strong>{`{case ID}`}</strong> under controller <strong>{`{org name}`}</strong>.
        </p>

        <ul style={{ paddingLeft: 20, color: "#d4d4d4" }}>
          <li>
            Lawful basis (GDPR Art.6):{" "}
            <strong>{`{6(1)(e) public interest / 6(1)(f) legitimate interest}`}</strong>
          </li>
          <li>
            Special category basis (GDPR Art.9):{" "}
            <strong>{`{9(2)(g) substantial public interest / 9(2)(f) legal claims}`}</strong>
          </li>
          <li>
            EU AI Act 2026 Annex III high-risk classification acknowledged: <strong>yes</strong>
          </li>
          <li>
            Retention period for embeddings:{" "}
            <strong>{`{30 days default; per-investigation override}`}</strong>
          </li>
          <li>
            Reaffirmation chain bound: ≤20 matches or ≤24h since last full attestation
          </li>
        </ul>

        <p>
          I have read and understood. <strong>Type &quot;I attest&quot;</strong> to proceed.
        </p>

        <input
          type="text"
          placeholder='Type "I attest" exactly...'
          aria-label="Attestation phrase"
          style={{
            width: "100%",
            padding: 12,
            marginTop: 12,
            background: "#0a0a0a",
            border: "1px solid #2a2a2a",
            borderRadius: 6,
            color: "#e5e5e5",
            fontFamily: "inherit",
            fontSize: 14,
            outline: "none",
            boxSizing: "border-box",
          }}
        />

        <p style={{ color: "#525252", fontSize: 11, marginTop: 24 }}>
          Frozen baseline copy from INTEGRATION-SPEC §8 + Camille §5.1.1. Full signing
          flow + Ed25519 + chain-emit lands in WI-0606 (Sprint 1 verb implementation).
        </p>
      </div>
    </main>
  );
}
'''

# Write all
written = 0
for rel, content in files.items():
    p = WEB_SRC / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    print(f"wrote {rel}")
    written += 1

# Now mount the PaletteMount in the investigator layout
layout_path = WEB_SRC / "app" / "(investigator)" / "layout.tsx"
layout = layout_path.read_text(encoding="utf-8")
if "PaletteMount" not in layout:
    # Insert import after the React import
    layout = layout.replace(
        'import type { ReactNode } from "react";',
        'import type { ReactNode } from "react";\nimport PaletteMount from "./_palette-mount";',
    )
    # Insert <PaletteMount /> right before the closing </div> of the outer fragment
    layout = layout.replace(
        '    </div>\n  );\n}\n',
        '      <PaletteMount />\n    </div>\n  );\n}\n',
    )
    layout_path.write_text(layout, encoding="utf-8")
    print("wired PaletteMount into (investigator)/layout.tsx")
    written += 1

# Clean .gitkeep if any
for gk in (WEB_SRC).rglob(".gitkeep"):
    if "components" in gk.parts or "lib" in gk.parts:
        gk.unlink()
        print(f"removed {gk.relative_to(ROOT).as_posix()}")

print(f"\ntotal: {written} files touched")
