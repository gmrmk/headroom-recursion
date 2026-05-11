"use client";

import { useMemo, useState } from "react";

import { useInvestigationStream } from "@/hooks/useInvestigationStream";
import type { InvestigationEvent, InvestigationEventType, StreamStatus } from "@/types/api";

interface EventStreamProps {
  investigationId: string;
}

const EVENT_COLORS: Record<InvestigationEventType, string> = {
  heartbeat: "#525252",
  "capture-started": "#60a5fa",
  "warc-written": "#a78bfa",
  "ed25519-signed": "#34d399",
  "rfc3161-stamped": "#34d399",
  "minio-stored": "#a78bfa",
  "ftm-entity-created": "#f59e0b",
  "wayback-queued": "#60a5fa",
  "tool-run-accepted": "#60a5fa",
  "tool-run-result": "#34d399",
  "tool-run-error": "#f87171",
};

const STATUS_LABEL: Record<StreamStatus, string> = {
  idle: "idle",
  connecting: "connecting...",
  open: "live",
  closed: "closed",
  error: "reconnecting...",
};

const STATUS_COLOR: Record<StreamStatus, string> = {
  idle: "#525252",
  connecting: "#f59e0b",
  open: "#34d399",
  closed: "#737373",
  error: "#f87171",
};

// R-9: dossier facets render Triage + Disprove as filter chips, not as
// first-class verbs (Margaret's Occam-cheaper alternative; see
// MARGARET-ROADMAP-2026-05-11). Under the property-vetting pivot, Disprove
// is the dominant workflow: "show me evidence this listing/owner is not
// who they claim."
type Facet = "all" | "triage" | "disprove";

const TRIAGE_EVENT_TYPES: ReadonlySet<InvestigationEventType> = new Set([
  // Discovery-phase events: the "what did we just find" view.
  "capture-started",
  "warc-written",
  "wayback-queued",
  "tool-run-accepted",
  "tool-run-result",
]);

function matchesFacet(event: InvestigationEvent, facet: Facet): boolean {
  if (facet === "all") {
    return event.event_type !== "heartbeat";
  }
  if (facet === "triage") {
    return TRIAGE_EVENT_TYPES.has(event.event_type);
  }
  // Disprove: events flagged as contradicting the subject. Until the
  // contradiction-detection adapter lands (Sprint 3+), the heuristic is
  // any event whose payload has a `contradicts` or `mismatch` key. The
  // chip surface exists today; the semantics deepen as adapters wire it.
  const p = event.payload;
  return (
    typeof p === "object" &&
    p !== null &&
    ("contradicts" in p || "mismatch" in p || event.event_type === "tool-run-error")
  );
}

export function EventStream({ investigationId }: EventStreamProps) {
  const { events, status } = useInvestigationStream(investigationId);
  const [facet, setFacet] = useState<Facet>("all");

  // Filter then reverse: facet is the user-selected slice, then newest-first
  // for the rendered list. The API guarantees monotonic sequence so
  // reverse-by-sequence gives stable order without depending on `ts`.
  const ordered = useMemo(() => {
    const filtered = events.filter((e) => matchesFacet(e, facet));
    return [...filtered].reverse();
  }, [events, facet]);

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 12,
        }}
      >
        <span
          aria-label={`stream status: ${STATUS_LABEL[status]}`}
          title={STATUS_LABEL[status]}
          style={{
            width: 8,
            height: 8,
            borderRadius: 4,
            background: STATUS_COLOR[status],
            display: "inline-block",
          }}
        />
        <span style={{ color: "#a3a3a3", fontSize: 12 }}>
          Stream {STATUS_LABEL[status]} &middot; {events.length} event
          {events.length === 1 ? "" : "s"}
        </span>
      </div>

      <div
        role="tablist"
        aria-label="Dossier facets"
        style={{ display: "flex", gap: 4, marginBottom: 12 }}
      >
        <FacetChip label="All" active={facet === "all"} onClick={() => setFacet("all")} />
        <FacetChip
          label="Triage"
          active={facet === "triage"}
          onClick={() => setFacet("triage")}
          hint="Discovery-phase events: captures, fetches, queued artifacts"
        />
        <FacetChip
          label="Disprove"
          active={facet === "disprove"}
          onClick={() => setFacet("disprove")}
          hint="Events flagged as contradicting the subject's claims"
        />
      </div>

      {ordered.length === 0 ? (
        <p style={{ color: "#525252", fontSize: 12 }}>
          {events.length === 0
            ? "No events yet. Trigger a tool run from the Tools tab to see live attestation events."
            : `No events match the ${facet} facet yet.`}
        </p>
      ) : (
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: 0,
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          {ordered.map((evt) => (
            <li
              key={evt.sequence}
              style={{
                display: "grid",
                gridTemplateColumns: "44px 1fr auto",
                gap: 12,
                padding: "8px 12px",
                background: "#0f0f0f",
                border: "1px solid #1f1f1f",
                borderRadius: 4,
                fontSize: 12,
                fontFamily: "ui-monospace, SFMono-Regular, monospace",
              }}
            >
              <span style={{ color: "#525252" }}>#{evt.sequence}</span>
              <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ color: EVENT_COLORS[evt.event_type], fontWeight: 600 }}>
                  {evt.event_type}
                </span>
                {Object.keys(evt.payload).length > 0 ? (
                  <span style={{ color: "#737373" }}>{summarizePayload(evt.payload)}</span>
                ) : null}
              </span>
              <span style={{ color: "#525252", whiteSpace: "nowrap" }}>{formatTs(evt.ts)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface FacetChipProps {
  label: string;
  active: boolean;
  onClick: () => void;
  hint?: string;
}

function FacetChip({ label, active, onClick, hint }: FacetChipProps) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      title={hint}
      style={{
        padding: "4px 10px",
        background: active ? "#1f1f1f" : "transparent",
        border: "1px solid",
        borderColor: active ? "#404040" : "#1f1f1f",
        borderRadius: 4,
        color: active ? "#e5e5e5" : "#a3a3a3",
        fontSize: 12,
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );
}

function summarizePayload(payload: Record<string, unknown>): string {
  const text = JSON.stringify(payload);
  return text.length > 96 ? `${text.slice(0, 93)}...` : text;
}

function formatTs(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return iso;
  }
  return d.toLocaleTimeString("en-US", { hour12: false });
}
