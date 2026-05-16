"use client";

import { useMemo, useState } from "react";

import { useInvestigationStream } from "@/hooks/useInvestigationStream";
import type { InvestigationEvent, InvestigationEventType, StreamStatus } from "@/types/api";

import { DossierExportButton } from "./dossier-export-button";
import { VerdictBanner } from "./verdict-banner";

// W4-FIG-GROUND (wave-4, Hideo §12 #3 Datadog Watchdog pattern): when a
// finding cites a specific time window, render that window as a tinted
// band overlay and fade events outside it to 60% opacity. The opacity
// contrast is the redundant signal that survives screen readers and
// colorblind users — the band is purely additive sighted-signal.
//
// Sprint-5 W4-TIMELINE will wire `time_window` from a real consumer; for
// now the prop ships unused.
interface TimeWindow {
  readonly start: string; // ISO 8601
  readonly end: string; // ISO 8601
  readonly label?: string; // optional caption shown at the band edge
}

interface EventStreamProps {
  readonly investigationId: string;
  readonly time_window?: TimeWindow | undefined;
}

function parseIsoMs(iso: string): number {
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? Number.NaN : t;
}

function isInTimeWindow(eventTs: string, window: TimeWindow): boolean {
  const ts = parseIsoMs(eventTs);
  const start = parseIsoMs(window.start);
  const end = parseIsoMs(window.end);
  if (Number.isNaN(ts) || Number.isNaN(start) || Number.isNaN(end)) {
    return false;
  }
  return ts >= start && ts <= end;
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
  // R-5 property-vetting event colors
  "geocode-match": "#fbbf24",
  "listing-match": "#fbbf24",
  "person-match": "#fbbf24",
  "breach-hit": "#f87171",
  "image-match": "#fbbf24",
  // W12.id identity-fabric event types (Phase 2, 2026-05-12).
  "tenant-match": "#22d3ee",
  "infra-fact": "#94a3b8",
  "email-posture": "#a78bfa",
  "sso-discovery": "#22d3ee",
  // W13.dk dork-sweep event type (Phase 5, 2026-05-12).
  "dork-hit": "#f472b6",
  // W4-SUB-BRAND wave-4 §4 (2026-05-12). Soft green: verification is a
  // trust-positive signal -- distinct from the alert-red breach-hit and
  // the alert-yellow listing/image-match (which surfaces a contradiction
  // candidate, not a verified floor).
  platform_verification_floor: "#86efac",
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
  // R-5 property-vetting Triage: evidence-of-presence events.
  "geocode-match",
  "listing-match",
  "person-match",
  "image-match",
]);

const DISPROVE_EVENT_TYPES: ReadonlySet<InvestigationEventType> = new Set([
  // R-5 property-vetting Disprove: evidence-against-claims events. The
  // breach-hit is the canonical "this subject is not who they claim"
  // signal in the property-vetting workflow.
  "breach-hit",
  "tool-run-error",
]);

function matchesFacet(event: InvestigationEvent, facet: Facet): boolean {
  if (facet === "all") {
    return event.event_type !== "heartbeat";
  }
  if (facet === "triage") {
    return TRIAGE_EVENT_TYPES.has(event.event_type);
  }
  // Disprove: typed event types AND legacy payload-flag heuristic (until
  // every adapter migrates to typed disprove events).
  if (DISPROVE_EVENT_TYPES.has(event.event_type)) {
    return true;
  }
  const p = event.payload;
  return typeof p === "object" && p !== null && ("contradicts" in p || "mismatch" in p);
}

// Hideo-IxD accept (2026-05-11 wave-3): collapse image-match events that
// share the same payload.image_url so a single "scan this photo" action
// renders as one accordion row instead of 5-20 stream lines. Threshold
// (>=2 events sharing image_url) is a candidate pending the 50-event
// empirical trace per Hideo's measurement-before-commitment principle.
const COLLAPSE_THRESHOLD = 2;

type RenderItem =
  | { kind: "event"; event: InvestigationEvent }
  | { kind: "group"; imageUrl: string; events: ReadonlyArray<InvestigationEvent> };

function buildRenderItems(ordered: ReadonlyArray<InvestigationEvent>): ReadonlyArray<RenderItem> {
  // Pass 1: bucket image-match events by payload.image_url.
  const groups = new Map<string, InvestigationEvent[]>();
  for (const e of ordered) {
    if (e.event_type !== "image-match") {
      continue;
    }
    const url = typeof e.payload?.image_url === "string" ? e.payload.image_url : "";
    if (!url) {
      continue;
    }
    const arr = groups.get(url) ?? [];
    arr.push(e);
    groups.set(url, arr);
  }
  // URLs that meet the collapse threshold.
  const collapsed = new Set<string>();
  for (const [url, evs] of groups.entries()) {
    if (evs.length >= COLLAPSE_THRESHOLD) {
      collapsed.add(url);
    }
  }
  // Pass 2: walk ordered, emit groups in first-event position.
  const out: RenderItem[] = [];
  const emitted = new Set<string>();
  for (const e of ordered) {
    if (e.event_type === "image-match") {
      const url = typeof e.payload?.image_url === "string" ? e.payload.image_url : "";
      if (url && collapsed.has(url)) {
        if (!emitted.has(url)) {
          out.push({ kind: "group", imageUrl: url, events: groups.get(url)! });
          emitted.add(url);
        }
        continue;
      }
    }
    out.push({ kind: "event", event: e });
  }
  return out;
}

export function EventStream({ investigationId, time_window }: EventStreamProps) {
  const { events, status } = useInvestigationStream(investigationId);
  const [facet, setFacet] = useState<Facet>("all");
  const [expandedUrls, setExpandedUrls] = useState<ReadonlySet<string>>(new Set());

  function toggleExpanded(url: string) {
    setExpandedUrls((prev) => {
      const next = new Set(prev);
      if (next.has(url)) {
        next.delete(url);
      } else {
        next.add(url);
      }
      return next;
    });
  }

  // Filter then reverse: facet is the user-selected slice, then newest-first
  // for the rendered list. The API guarantees monotonic sequence so
  // reverse-by-sequence gives stable order without depending on `ts`.
  const ordered = useMemo(() => {
    const filtered = events.filter((e) => matchesFacet(e, facet));
    return [...filtered].reverse();
  }, [events, facet]);

  const renderItems = useMemo(() => buildRenderItems(ordered), [ordered]);

  return (
    <div>
      <VerdictBanner events={events} />

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
        <span style={{ marginLeft: "auto" }}>
          <DossierExportButton
            events={events}
            context={{ investigationId }}
          />
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

      {renderItems.length === 0 ? (
        <p style={{ color: "#525252", fontSize: 12 }}>
          {events.length === 0
            ? "No events yet. Trigger a tool run from the Tools tab to see live attestation events."
            : `No events match the ${facet} facet yet.`}
        </p>
      ) : (
        <div style={{ position: "relative" }}>
          {time_window ? (
            <FigGroundBand window={time_window} />
          ) : null}
          <ul
            style={{
              listStyle: "none",
              padding: 0,
              margin: 0,
              display: "flex",
              flexDirection: "column",
              gap: 6,
              position: "relative",
            }}
          >
            {renderItems.map((item) => {
              if (item.kind === "event") {
                const inWindow = time_window
                  ? isInTimeWindow(item.event.ts, time_window)
                  : true;
                return (
                  <EventRow
                    key={item.event.sequence}
                    event={item.event}
                    dimmed={!inWindow}
                  />
                );
              }
              // Group is "in window" if ANY child event falls inside —
              // dimming an aggregate row that contains in-window evidence
              // would hide the finding.
              const groupInWindow = time_window
                ? item.events.some((e) => isInTimeWindow(e.ts, time_window))
                : true;
              return (
                <ImageMatchGroup
                  key={`group:${item.imageUrl}`}
                  imageUrl={item.imageUrl}
                  events={item.events}
                  expanded={expandedUrls.has(item.imageUrl)}
                  onToggle={() => toggleExpanded(item.imageUrl)}
                  dimmed={!groupInWindow}
                />
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

interface FigGroundBandProps {
  readonly window: TimeWindow;
}

// Band overlay: tinted region behind the event rows that survives PDF
// export (print-color-adjust: exact). The opacity contrast on the rows
// themselves is the redundant signal for screen-reader + colorblind
// users — this band is sighted-signal only.
function FigGroundBand({ window }: FigGroundBandProps) {
  const labelText =
    window.label ?? `${window.start} to ${window.end}`;
  return (
    <div
      role="img"
      aria-label={`Time window: ${labelText}`}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: "rgba(252, 211, 77, 0.18)",
        border: "1px solid rgba(252, 211, 77, 0.4)",
        borderRadius: 4,
        pointerEvents: "none",
        zIndex: 0,
        // Camille print-export gate: Chromium drops background colors on
        // print by default. print-color-adjust: exact forces preservation
        // so the band survives the evidence-bundle PDF.
        printColorAdjust: "exact",
        WebkitPrintColorAdjust: "exact",
      }}
    >
      {window.label ? (
        <span
          style={{
            position: "absolute",
            top: 4,
            left: 8,
            fontSize: 10,
            fontFamily: "ui-monospace, SFMono-Regular, monospace",
            color: "#fbbf24",
            background: "#0a0a0a",
            padding: "1px 6px",
            borderRadius: 3,
            border: "1px solid rgba(252, 211, 77, 0.4)",
            pointerEvents: "none",
          }}
        >
          {window.label}
        </span>
      ) : null}
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

const ROW_STYLE: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "44px 1fr auto",
  gap: 12,
  padding: "8px 12px",
  background: "#0f0f0f",
  border: "1px solid #1f1f1f",
  borderRadius: 4,
  fontSize: 12,
  fontFamily: "ui-monospace, SFMono-Regular, monospace",
};

// Mei-Lan M1 (2026-05-11): payload keys ending in `_rel` carry a forward-
// slash, rel-to-data path that the API's /files/{rel} surface serves
// inline (Camille's allowlist + containment, see apps/api/.../files.py).
// Any image adapter that writes an artifact under data/<allowed-subdir>/
// opts into inline rendering by emitting a *_rel field -- no frontend
// change needed per new adapter, as long as the subdir is on the
// allowlist.
const PREVIEW_LABELS: Record<string, string> = {
  flipped_rel: "flipped variant",
  ela_rel: "ELA glow-map",
};

function collectPreviews(
  payload: Record<string, unknown>,
): ReadonlyArray<{ rel: string; label: string }> {
  const out: Array<{ rel: string; label: string }> = [];
  for (const [k, v] of Object.entries(payload)) {
    if (!k.endsWith("_rel") || typeof v !== "string" || !v) {
      continue;
    }
    out.push({ rel: v, label: PREVIEW_LABELS[k] ?? k.replace(/_rel$/, "") });
  }
  return out;
}

function EventRow({
  event,
  dimmed = false,
}: {
  event: InvestigationEvent;
  dimmed?: boolean;
}) {
  const previews = collectPreviews(event.payload);
  // Fallback for older synthetic events that emit only flipped_path (no
  // _rel sibling). Drop when the synthetic-only path is retired.
  const flippedPath =
    previews.length === 0 && typeof event.payload?.flipped_path === "string"
      ? event.payload.flipped_path
      : "";
  // W4-FIG-GROUND: events outside the cited time_window fade to 60% so
  // colorblind and screen-reader users (who can't see the band tint) get
  // the same figure-ground signal as sighted users.
  const rowStyle: React.CSSProperties = dimmed
    ? { ...ROW_STYLE, opacity: 0.6, position: "relative", zIndex: 1 }
    : { ...ROW_STYLE, position: "relative", zIndex: 1 };
  return (
    <li style={rowStyle}>
      <span style={{ color: "#525252" }}>#{event.sequence}</span>
      <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{ color: EVENT_COLORS[event.event_type], fontWeight: 600 }}>
          {event.event_type}
        </span>
        {Object.keys(event.payload).length > 0 ? (
          <span style={{ color: "#737373" }}>{summarizePayload(event.payload)}</span>
        ) : null}
        {previews.length > 0 ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
            {previews.map((p) => (
              <a
                key={p.rel}
                href={`/api/files/${p.rel}`}
                target="_blank"
                rel="noopener noreferrer"
                title={p.label}
                style={{ display: "inline-block" }}
              >
                <img
                  src={`/api/files/${p.rel}`}
                  alt={p.label}
                  loading="lazy"
                  style={{
                    maxHeight: 96,
                    maxWidth: 160,
                    border: "1px solid #2a2a2a",
                    borderRadius: 4,
                    background: "#1a1a1a",
                    display: "block",
                  }}
                />
              </a>
            ))}
          </div>
        ) : flippedPath ? (
          <a
            href={`file:///${flippedPath.replace(/\\/g, "/")}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "#60a5fa", fontSize: 11 }}
          >
            open flipped variant
          </a>
        ) : null}
      </span>
      <span style={{ color: "#525252", whiteSpace: "nowrap" }}>{formatTs(event.ts)}</span>
    </li>
  );
}

interface ImageMatchGroupProps {
  imageUrl: string;
  events: ReadonlyArray<InvestigationEvent>;
  expanded: boolean;
  onToggle: () => void;
  dimmed?: boolean;
}

function ImageMatchGroup({
  imageUrl,
  events,
  expanded,
  onToggle,
  dimmed = false,
}: ImageMatchGroupProps) {
  // Engine count = distinct payload.source values.
  const sources = new Set<string>();
  for (const e of events) {
    if (typeof e.payload?.source === "string") {
      sources.add(e.payload.source);
    }
  }
  const ts = events[0]?.ts ?? "";
  const sourceList = Array.from(sources).join(", ") || "—";
  return (
    <li
      style={{
        background: "#0f0f0f",
        border: "1px solid #1f1f1f",
        borderRadius: 4,
        overflow: "hidden",
        opacity: dimmed ? 0.6 : 1,
        position: "relative",
        zIndex: 1,
      }}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        style={{
          display: "grid",
          gridTemplateColumns: "44px 1fr auto",
          gap: 12,
          width: "100%",
          padding: "8px 12px",
          background: "transparent",
          border: "none",
          color: "inherit",
          fontSize: 12,
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          textAlign: "left",
          cursor: "pointer",
        }}
      >
        <span style={{ color: "#525252" }}>{expanded ? "▾" : "▸"}</span>
        <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ color: "#fbbf24", fontWeight: 600 }}>
            image-match × {events.length} ({sources.size}{" "}
            {sources.size === 1 ? "engine" : "engines"}: {sourceList})
          </span>
          <span style={{ color: "#737373", wordBreak: "break-all" }}>{imageUrl}</span>
        </span>
        <span style={{ color: "#525252", whiteSpace: "nowrap" }}>{formatTs(ts)}</span>
      </button>
      {expanded ? (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: "0 0 6px 0",
            display: "flex",
            flexDirection: "column",
            gap: 4,
            borderTop: "1px solid #1f1f1f",
            background: "#0a0a0a",
          }}
        >
          {events.map((e) => (
            <li
              key={e.sequence}
              style={{
                ...ROW_STYLE,
                background: "transparent",
                border: "none",
                marginLeft: 24,
                padding: "6px 12px",
              }}
            >
              <span style={{ color: "#525252" }}>#{e.sequence}</span>
              <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ color: "#737373", fontSize: 11 }}>
                  source: {String(e.payload?.source ?? "?")}
                </span>
                <span style={{ color: "#a3a3a3" }}>{summarizePayload(e.payload)}</span>
              </span>
              <span style={{ color: "#525252", whiteSpace: "nowrap" }}>{formatTs(e.ts)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

