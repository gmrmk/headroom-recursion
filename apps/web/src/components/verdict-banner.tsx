"use client";

import { useMemo } from "react";

import type { Verdict } from "@/lib/verdict";
import { BUCKET_COLOR, synthesizeVerdict } from "@/lib/verdict";
import type { InvestigationEvent } from "@/types/api";

interface VerdictBannerProps {
  events: ReadonlyArray<InvestigationEvent>;
}

// Margaret's verdict synthesizer surfaced as a live banner above the
// event stream. Updates as new events arrive (React re-renders on
// `events` prop change). The verdict is heuristic, not proof -- the
// banner's `why` line tells the investigator exactly why this bucket
// was chosen.
//
// Hidden until at least one event-of-interest arrives (person-match,
// breach-hit, or tool-run-result). Prevents a premature "low-footprint"
// flash on an empty investigation.
export function VerdictBanner({ events }: VerdictBannerProps) {
  const verdict = useMemo<Verdict | null>(() => synthesizeVerdict(events), [events]);
  if (!verdict) {
    return null;
  }
  const color = BUCKET_COLOR[verdict.bucket];
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: "10px 14px",
        marginBottom: 12,
        background: "#0f0f0f",
        border: `1px solid ${color}40`,
        borderLeft: `4px solid ${color}`,
        borderRadius: 4,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ color: "#a3a3a3", fontSize: 11, letterSpacing: 0.5 }}>
          VERDICT
        </span>
        <span style={{ color, fontWeight: 600, fontSize: 14 }}>{verdict.bucket}</span>
        <span style={{ color: "#737373", fontSize: 11 }}>
          {verdict.confidence} confidence
        </span>
        <SignalChips signals={verdict.signals} />
      </div>
      <div style={{ color: "#a3a3a3", fontSize: 12 }}>{verdict.why}</div>
      <div style={{ color: "#737373", fontSize: 11, fontStyle: "italic" }}>
        next → {verdict.next}
      </div>
    </div>
  );
}

interface SignalChipsProps {
  signals: Verdict["signals"];
}

function SignalChips({ signals }: SignalChipsProps) {
  // Tiny inline chips so the investigator sees which of the four
  // signals are firing at a glance.
  const chips: Array<{ label: string; on: boolean }> = [
    { label: "identity", on: signals.identity },
    { label: "behavior", on: signals.behavior },
    { label: "compromise", on: signals.compromise },
    { label: "consumer", on: signals.consumer_tail },
  ];
  return (
    <span
      style={{
        display: "inline-flex",
        gap: 4,
        marginLeft: "auto",
      }}
    >
      {chips.map((c) => (
        <span
          key={c.label}
          style={{
            padding: "1px 6px",
            borderRadius: 3,
            fontSize: 10,
            background: c.on ? "#1f1f1f" : "transparent",
            color: c.on ? "#e5e5e5" : "#525252",
            border: "1px solid",
            borderColor: c.on ? "#404040" : "#1f1f1f",
          }}
        >
          {c.on ? "✓ " : "— "}
          {c.label}
        </span>
      ))}
    </span>
  );
}
