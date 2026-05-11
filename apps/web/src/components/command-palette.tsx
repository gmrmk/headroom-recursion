"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  ADAPTERS,
  ADAPTER_SELECT_EVENT,
  GROUP_LABELS,
  GROUP_ORDER,
  WORKFLOWS,
  type AdapterGroup,
  type AdapterMeta,
  type AdapterSelectDetail,
  type WorkflowMeta,
} from "../lib/adapters-catalog";
import { KEY_BINDINGS, matchesBinding } from "../lib/keyboard";

/**
 * cmd-K palette — minimal substrate per ADR-0017 Sprint-1 scope.
 *
 * What this commit ships (Sprint-3 wave-3, Iris re-open trigger fix):
 *   - cmd-K opens; cmd-Shift-K opens scoped; Esc closes (already wired
 *     in the prior stub).
 *   - Input filters adapters by id + label + hint (substring match,
 *     case-insensitive). Ranking is naive (exact-prefix > substring);
 *     the 6-input weighted ranker per ADR-0017 §4 is WI-0606.
 *   - Arrow up/down navigate; Enter dispatches ADAPTER_SELECT_EVENT
 *     and closes; the RunToolForm listens and updates its selection.
 *   - Empty state lists adapters grouped by Iris's six primitives.
 *
 * Deferred to WI-0606 per ADR-0017:
 *   - W1-W8 workflow grid empty-state
 *   - Recency + frequency + context_fit + opsec_penalty ranking inputs
 *   - cmdk library integration (paco-cmdk / shadcn)
 *   - Scoped-mode filtering by current investigation context
 */

export interface CommandPaletteProps {
  /** When true, force-open (e.g. invoked from a sister component). */
  forceOpen?: boolean;
}

export function CommandPalette({ forceOpen = false }: CommandPaletteProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [scoped, setScoped] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

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
        setQuery("");
        setActiveIdx(0);
        setIsOpen(true);
      } else if (matchesBinding(event, scopedBinding)) {
        event.preventDefault();
        setScoped(true);
        setQuery("");
        setActiveIdx(0);
        setIsOpen(true);
      } else if (isOpen && matchesBinding(event, dismissBinding)) {
        event.preventDefault();
        setIsOpen(false);
      }
    }

    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [isOpen]);

  // Auto-focus the input when palette opens
  useEffect(() => {
    if (isOpen) {
      // Defer focus to next tick so the input is in the DOM
      const t = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [isOpen]);

  // Filter + rank adapters by the current query. Score: 100 for exact-id
  // prefix, 50 for label prefix, 10 for substring match (id/label/hint),
  // 0 otherwise. Stable secondary order = catalog order. ADR-0017 §4
  // calls for a 6-input weighted ranker; this is the minimal first cut.
  const filtered: ReadonlyArray<AdapterMeta> = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      return ADAPTERS;
    }
    return ADAPTERS.map((a) => {
      const lid = a.id.toLowerCase();
      const lab = a.label.toLowerCase();
      const hint = a.hint.toLowerCase();
      let score = 0;
      if (lid.startsWith(q)) {
        score = 100;
      } else if (lab.startsWith(q)) {
        score = 50;
      } else if (lid.includes(q) || lab.includes(q) || hint.includes(q)) {
        score = 10;
      }
      return { a, score };
    })
      .filter((row) => row.score > 0)
      .sort((x, y) => y.score - x.score)
      .map((row) => row.a);
  }, [query]);

  // Keep activeIdx in range as the filtered list changes
  useEffect(() => {
    if (activeIdx >= filtered.length) {
      setActiveIdx(Math.max(0, filtered.length - 1));
    }
  }, [filtered, activeIdx]);

  function dispatchSelect(adapterId: string) {
    const detail: AdapterSelectDetail = { adapterId };
    window.dispatchEvent(new CustomEvent(ADAPTER_SELECT_EVENT, { detail }));
    setIsOpen(false);
    setQuery("");
  }

  function handleInputKey(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIdx((i) => Math.min(filtered.length - 1, i + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIdx((i) => Math.max(0, i - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const pick = filtered[activeIdx];
      if (pick) {
        dispatchSelect(pick.id);
      }
    }
  }

  if (!isOpen) return null;

  // Pre-group filtered set for render. When the user is typing, the
  // ranked-flat order matters more than grouping; we still show group
  // headers but in score-determined order rather than catalog order.
  const grouped = new Map<AdapterGroup, AdapterMeta[]>();
  for (const a of filtered) {
    const arr = grouped.get(a.group) ?? [];
    arr.push(a);
    grouped.set(a.group, arr);
  }

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
          marginTop: "12vh",
          width: 720,
          maxWidth: "92vw",
          maxHeight: "76vh",
          display: "flex",
          flexDirection: "column",
          background: "#141414",
          border: "1px solid #2a2a2a",
          borderRadius: 8,
          color: "#e5e5e5",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 14px",
            borderBottom: "1px solid #1f1f1f",
          }}
        >
          <span style={{ color: "#737373", fontSize: 11 }}>
            {scoped ? "Scoped (current investigation)" : "Root"}
          </span>
          <span style={{ color: "#525252", fontSize: 11 }}>
            · {filtered.length}/{ADAPTERS.length} adapters
          </span>
          <span style={{ marginLeft: "auto", color: "#525252", fontSize: 11 }}>
            ↑↓ navigate · Enter select · Esc close
          </span>
        </div>
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActiveIdx(0);
          }}
          onKeyDown={handleInputKey}
          placeholder={
            scoped ? "Search in this investigation..." : "Type to filter (id, label, hint)..."
          }
          aria-label="Palette search"
          style={{
            width: "100%",
            padding: "12px 14px",
            background: "#0a0a0a",
            border: "none",
            borderBottom: "1px solid #1f1f1f",
            color: "#e5e5e5",
            fontSize: 14,
            outline: "none",
            boxSizing: "border-box",
          }}
        />
        <div
          ref={listRef}
          style={{
            overflowY: "auto",
            flex: 1,
            padding: "4px 0",
          }}
        >
          {query.trim() === "" ? (
            // ADR-0017 §3 empty-state: render the W1-W9 workflow grid.
            // Click/Enter on a workflow fills the search with its prefix
            // so the filtered list below shows related adapters.
            <WorkflowGrid
              workflows={WORKFLOWS}
              onPick={(w) => {
                setQuery(w.prefix);
                setActiveIdx(0);
                inputRef.current?.focus();
              }}
            />
          ) : filtered.length === 0 ? (
            <p style={{ color: "#525252", fontSize: 12, padding: "12px 14px" }}>
              No adapters match "{query}". Try a substring of the adapter
              id (e.g. "yandex", "exif", "follower"), or a workflow prefix
              from the W1-W9 grid (un, em, ph, im, do, pe, fa, ge, pv).
            </p>
          ) : (
            GROUP_ORDER.map((g) => {
              const items = grouped.get(g);
              if (!items || items.length === 0) return null;
              return (
                <div key={g} style={{ padding: "4px 0" }}>
                  <div
                    style={{
                      padding: "4px 14px",
                      color: "#525252",
                      fontSize: 10,
                      textTransform: "uppercase",
                      letterSpacing: 0.5,
                    }}
                  >
                    {GROUP_LABELS[g]}
                  </div>
                  {items.map((a) => {
                    const flatIdx = filtered.indexOf(a);
                    const isActive = flatIdx === activeIdx;
                    return (
                      <button
                        type="button"
                        key={a.id}
                        onMouseDown={(e) => {
                          // mousedown vs click so the input's blur doesn't
                          // fire first and re-trigger close
                          e.preventDefault();
                          dispatchSelect(a.id);
                        }}
                        onMouseEnter={() => setActiveIdx(flatIdx)}
                        aria-selected={isActive}
                        style={{
                          display: "block",
                          width: "100%",
                          textAlign: "left",
                          padding: "6px 14px",
                          background: isActive ? "#1f1f1f" : "transparent",
                          border: "none",
                          color: "#e5e5e5",
                          fontSize: 13,
                          cursor: "pointer",
                          fontFamily: "inherit",
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                          <span style={{ fontWeight: 600 }}>{a.label}</span>
                          <span style={{ color: "#525252", fontSize: 11 }}>{a.id}</span>
                        </div>
                        <div style={{ color: "#737373", fontSize: 11, marginTop: 2 }}>
                          {a.hint}
                        </div>
                      </button>
                    );
                  })}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

interface WorkflowGridProps {
  workflows: ReadonlyArray<WorkflowMeta>;
  onPick: (w: WorkflowMeta) => void;
}

function WorkflowGrid({ workflows, onPick }: WorkflowGridProps) {
  return (
    <div style={{ padding: "12px 14px" }}>
      <div
        style={{
          color: "#525252",
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: 0.5,
          marginBottom: 8,
        }}
      >
        Workflows (ADR-0017 §3) — click to filter
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 8,
        }}
      >
        {workflows.map((w) => (
          <button
            type="button"
            key={w.id}
            onMouseDown={(e) => {
              e.preventDefault();
              onPick(w);
            }}
            aria-label={`Workflow ${w.name}`}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 4,
              padding: "10px 12px",
              background: "#0a0a0a",
              border: "1px solid #1f1f1f",
              borderRadius: 4,
              color: "#e5e5e5",
              fontSize: 12,
              fontFamily: "inherit",
              textAlign: "left",
              cursor: "pointer",
            }}
          >
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span
                style={{
                  color: "#fbbf24",
                  fontFamily: "ui-monospace, SFMono-Regular, monospace",
                  fontWeight: 700,
                }}
              >
                {w.prefix}
              </span>
              <span style={{ fontWeight: 600 }}>{w.name}</span>
            </div>
            <span style={{ color: "#737373", fontSize: 11 }}>{w.summary}</span>
          </button>
        ))}
      </div>
      <div style={{ color: "#525252", fontSize: 11, marginTop: 10 }}>
        Or type to search {ADAPTERS.length} adapters by id, label, or hint.
      </div>
    </div>
  );
}
