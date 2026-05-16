// Verdict synthesizer (TS port) -- Margaret's free-stack rubric.
//
// 1:1 mirror of the Python `_synthesize_verdict` in
// `tools/dev/smoke-w11-em.py`. The TS port reduces over the LIVE event
// stream visible to the web client; the Python smoke reduces over
// per-adapter counts collected in-process. Different inputs, same
// rubric, same buckets, same precedence.
//
// Six buckets, ordered by rule precedence (first match wins):
//   1. compromised-real  identity + compromise
//   2. real-careful      identity + behavior + zero compromise + zero consumer
//   3. real-active       identity + behavior + (maybe compromise) consumer
//   4. suspicious-churn  zero identity + zero behavior + compromise
//   5. low-footprint     zero everything
//   6. mixed             fallback
//
// Signals are derived from event-type rollups + the `source` field in
// payloads. Stable across adapter reshape because no raw upstream
// fields are referenced -- only the (event_type, source) pair.

import type { InvestigationEvent } from "@/types/api";

export type VerdictBucket =
  | "compromised-real"
  | "real-careful"
  | "real-active"
  | "suspicious-churn"
  | "low-footprint"
  | "mixed";

export type VerdictConfidence = "high" | "medium" | "low";

export interface VerdictSignals {
  readonly identity: boolean;
  readonly behavior: boolean;
  readonly compromise: boolean;
  readonly consumer_tail: boolean;
}

export interface Verdict {
  readonly bucket: VerdictBucket;
  readonly confidence: VerdictConfidence;
  readonly why: string;
  readonly next: string;
  readonly signals: VerdictSignals;
}

interface VerdictRule {
  readonly bucket: VerdictBucket;
  readonly test: (s: VerdictSignals) => boolean;
  readonly confidence: VerdictConfidence;
  readonly why: string;
  readonly next: string;
}

const RULES: ReadonlyArray<VerdictRule> = [
  {
    bucket: "compromised-real",
    test: (s) => s.identity && s.compromise,
    confidence: "high",
    why: "Owner-attested identity AND infostealer-log compromise",
    next: "Real person whose machine was infected. Trust identity claims; flag compromise context in dossier.",
  },
  {
    bucket: "real-careful",
    test: (s) => s.identity && s.behavior && !s.compromise && !s.consumer_tail,
    confidence: "high",
    why: "Owner-attested identity + behavioral confirmation + no compromise + no consumer-service tail",
    next: "Real long-lived person with operational discipline. Identity reliably anchored.",
  },
  {
    bucket: "real-active",
    test: (s) => s.identity && s.behavior && !s.compromise,
    confidence: "high",
    why: "Identity + behavior, possibly some consumer-service tail",
    next: "Typical real-person profile. Identity anchored; cross-check claims via Gravatar/GitHub URLs.",
  },
  {
    bucket: "suspicious-churn",
    test: (s) => !s.identity && !s.behavior && s.compromise,
    confidence: "medium",
    why: "Zero identity + zero behavior + compromise hits",
    next: "Likely churn or throwaway account. Flag as anomaly; deep-vet listing photos + address records.",
  },
  {
    bucket: "low-footprint",
    test: (s) => !s.identity && !s.behavior && !s.compromise && !s.consumer_tail,
    confidence: "medium",
    why: "Zero hits across every leg (identity, behavior, compromise, consumer)",
    next: "Email gives no identity bridge. Pivot to address records, listing photos, phone, host display name.",
  },
  {
    bucket: "mixed",
    test: () => true,
    confidence: "low",
    why: "Partial signals; pattern doesn't match a clean bucket",
    next: "Read the per-adapter events directly; rubric uncertain.",
  },
];

export function buildSignalsFromEvents(
  events: ReadonlyArray<InvestigationEvent>,
): VerdictSignals {
  let gravatar = false;
  let github = false;
  let userScanner = false;
  let compromise = false;
  for (const e of events) {
    const t = e.event_type;
    const source =
      typeof e.payload?.source === "string" ? (e.payload.source as string) : "";
    if (t === "person-match") {
      if (source === "gravatar") gravatar = true;
      else if (source === "github_commits") github = true;
      else if (source === "user_scanner") userScanner = true;
    } else if (t === "breach-hit") {
      // Any breach-hit fires the compromise signal. HIBP omits `source`;
      // Hudson Rock and IntelBase set it explicitly. Either way, the
      // verdict logic ORs them.
      compromise = true;
    }
  }
  return {
    identity: gravatar || github,
    behavior: github,
    compromise,
    consumer_tail: userScanner,
  };
}

export function synthesizeVerdict(
  events: ReadonlyArray<InvestigationEvent>,
): Verdict | null {
  // Only show a verdict once at least one event-type-of-interest has
  // arrived; otherwise the banner stays hidden so the empty-investigation
  // case doesn't show "low-footprint" prematurely.
  const interesting = events.some(
    (e) =>
      e.event_type === "person-match" ||
      e.event_type === "breach-hit" ||
      e.event_type === "tool-run-result",
  );
  if (!interesting) {
    return null;
  }
  const signals = buildSignalsFromEvents(events);
  for (const rule of RULES) {
    if (rule.test(signals)) {
      return {
        bucket: rule.bucket,
        confidence: rule.confidence,
        why: rule.why,
        next: rule.next,
        signals,
      };
    }
  }
  return null;
}

// Bucket -> color hint for the UI. The dashboard uses these to color the
// banner so the investigator can read the verdict at a glance.
export const BUCKET_COLOR: Record<VerdictBucket, string> = {
  "compromised-real": "#fbbf24",
  "real-careful": "#34d399",
  "real-active": "#34d399",
  "suspicious-churn": "#f87171",
  "low-footprint": "#fbbf24",
  mixed: "#60a5fa",
};

// Business-impact translation layer -- methodology §31.3 risk-translation
// matrix applied to our verdict buckets. Adopted 2026-05-12 (Margaret
// Phase 1 doctrine, Mei-Lan ownership). The technical `why` line is for
// the investigator's mental model; this layer is what they'd tell a
// stakeholder who doesn't speak OSINT.
const BUCKET_BUSINESS_IMPACT: Record<VerdictBucket, string> = {
  "compromised-real":
    "This is a real person whose account has been compromised. Stolen credentials may be in active use; messages from this host could be the attacker, not the owner. Treat any urgent or unusual asks with extra scrutiny.",
  "real-careful":
    "This appears to be a real person managing their own digital footprint deliberately. No compromise signals; identity is consistent across sources. Normal counterparty-risk posture is appropriate.",
  "real-active":
    "This is a real person with a large, active online presence. Many surfaces means more attack surface, but no specific compromise signal. Standard hygiene applies.",
  "suspicious-churn":
    "Identity signals are thin while compromise signals are present. Pattern is consistent with a compromised, impersonated, or fabricated account. Defer high-trust actions until corroborated by a second channel (video call, in-person, known-good contact).",
  "low-footprint":
    "Almost no digital trail in any source we checked. Could be a deliberately private real person OR a fabricated identity. Verify through a non-digital channel before extending trust.",
  mixed:
    "Signals don't fit a clean pattern. Treat as inconclusive; gather one more independent data point before deciding.",
};

/**
 * Plain-language business-impact translation of a verdict bucket.
 *
 * Adopted from osint-methodology §31.3 (risk-translation matrix). The
 * verdict's `why` line is the technical reasoning; this is what to tell
 * someone who needs the consequence, not the analysis.
 */
export function businessImpactForVerdict(verdict: Verdict): string {
  return BUCKET_BUSINESS_IMPACT[verdict.bucket];
}
