"use client";

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
