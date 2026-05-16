"use client";

import type React from "react";
import { useMemo, useState } from "react";

import { Card } from "@/components/ui/Card";
import { MetaText } from "@/components/ui/MetaText";
import { Stack } from "@/components/ui/Stack";
import {
  assessConfidence,
  CONFIDENCE_COLOR,
  CONFIDENCE_LABEL,
  nextUpgradeHint,
  type Asset,
  type Confidence,
} from "@/lib/asset-graph";
import {
  buildReportShape,
  type Finding,
  type Section,
  type Severity,
} from "@/lib/dossier-shape";
import { businessImpactForVerdict, synthesizeVerdict } from "@/lib/verdict";
import type { InvestigationEvent } from "@/types/api";

/**
 * InvestigationReport — the one-glance live dashboard the investigator
 * actually reads. Phase 4 of dashboard redesign 2026-05-11.
 *
 * Consumes the same `buildReportShape` projection that the HTML
 * serializer uses (single source of truth). As events stream in via
 * SSE, the projection re-runs and findings appear; new findings get
 * the `.stream-in` fade-in animation per IntelBase recon recommendation.
 *
 * Layout (top to bottom):
 *   - Verdict card (full-width, accent border by bucket color)
 *   - Section cards: Identity / Behavior / Compromise / Property / Visual
 *     - Empty sections hidden (no "Visual: 0 findings" noise)
 *     - Each section is a Card; findings render as rows separated by
 *       hairline dividers (NOT card-on-card)
 *   - Adapter errors collapsed at the bottom
 *   - Export buttons in the top header
 */

interface InvestigationReportProps {
  readonly events: ReadonlyArray<InvestigationEvent>;
  readonly investigationId: string;
}

export function InvestigationReport({
  events,
}: InvestigationReportProps) {
  const shape = useMemo(() => buildReportShape(events), [events]);
  const verdict = useMemo(() => synthesizeVerdict(events), [events]);

  const visibleSections = shape.sections.filter((s) => s.findings.length > 0);

  return (
    <Stack gap="4">
      {/* Top bar: section meta + event count -- no save affordance.
       * Per stealth-first directive 2026-05-11: the dossier is read,
       * understood, closed. Nothing about an investigation outlives
       * the tab close.
       */}
      <Stack direction="row" align="baseline" gap="3">
        <MetaText variant="section">Investigation report</MetaText>
        <span
          style={{
            fontSize: "var(--text-xs)",
            color: "var(--color-text-dim)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {shape.event_count} event{shape.event_count === 1 ? "" : "s"}
        </span>
      </Stack>

      {/* Verdict block */}
      <VerdictCard verdict={verdict} />

      {/* Empty-investigation hint */}
      {!shape.has_any_findings && verdict === null ? (
        <Card padding="lg" variant="plain">
          <Stack gap="2" align="center">
            <MetaText variant="kicker">No findings yet</MetaText>
            <p
              style={{
                margin: 0,
                color: "var(--color-text-muted)",
                fontSize: "var(--text-sm)",
                textAlign: "center",
              }}
            >
              Fill the form above and click Investigate. Findings will
              fade in here as workflows complete.
            </p>
          </Stack>
        </Card>
      ) : null}

      {/* Section cards */}
      {visibleSections.map((section) => (
        <SectionCard key={section.id} section={section} />
      ))}

      {/* Errors at the bottom (collapsible visual weight) */}
      {shape.errors.length > 0 ? (
        <Card padding="md" variant="warn">
          <Stack gap="2">
            <MetaText variant="section">
              Adapter errors ({shape.errors.length})
            </MetaText>
            <Stack gap="1" as="ul" style={{ margin: 0, padding: 0, listStyle: "none" }}>
              {shape.errors.slice(0, 10).map((err, idx) => (
                <li
                  key={idx}
                  style={{
                    fontSize: "var(--text-xs)",
                    color: "var(--color-text-secondary)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {err.adapter_id ? (
                    <code
                      style={{
                        color: "var(--color-text-muted)",
                        marginRight: "var(--space-2)",
                      }}
                    >
                      {err.adapter_id}
                    </code>
                  ) : null}
                  {err.reason}
                </li>
              ))}
            </Stack>
          </Stack>
        </Card>
      ) : null}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Verdict card — Margaret's rubric, prominently rendered
// ---------------------------------------------------------------------------

function VerdictCard({ verdict }: { verdict: ReturnType<typeof synthesizeVerdict> }) {
  if (!verdict) return null;
  const variant: "accent" | "warn" | "danger" | "success" = (() => {
    switch (verdict.bucket) {
      case "real-careful":
      case "real-active":
        return "success";
      case "compromised-real":
      case "low-footprint":
        return "warn";
      case "suspicious-churn":
        return "danger";
      default:
        return "accent";
    }
  })();
  const accentColor = (() => {
    switch (variant) {
      case "success":
        return "var(--color-success)";
      case "warn":
        return "var(--color-warn)";
      case "danger":
        return "var(--color-danger)";
      default:
        return "var(--color-accent)";
    }
  })();
  return (
    <Card padding="lg" variant={variant} style={{ position: "relative" }}>
      <Stack gap="2">
        <Stack direction="row" gap="3" align="baseline">
          <MetaText variant="section">Verdict</MetaText>
          <span
            style={{
              fontSize: "var(--text-xl)",
              fontWeight: 700,
              color: accentColor,
              letterSpacing: "-0.01em",
            }}
          >
            {verdict.bucket}
          </span>
          <span
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--color-text-muted)",
            }}
          >
            {verdict.confidence} confidence
          </span>
          <span style={{ marginLeft: "auto" }}>
            <SignalChips signals={verdict.signals} />
          </span>
        </Stack>
        <p
          style={{
            margin: 0,
            color: "var(--color-text-secondary)",
            fontSize: "var(--text-sm)",
          }}
        >
          {verdict.why}
        </p>
        {/* Business-impact translation -- osint-methodology §31.3,
            adopted 2026-05-12 (Margaret Phase 1 doctrine). The technical
            `why` above is for the investigator's mental model; this line
            is what the investigator would tell a stakeholder. */}
        <p
          style={{
            margin: 0,
            color: "var(--color-text-primary)",
            fontSize: "var(--text-sm)",
            lineHeight: 1.5,
            borderLeft: `2px solid ${accentColor}`,
            paddingLeft: "var(--space-3)",
          }}
        >
          {businessImpactForVerdict(verdict)}
        </p>
        <p
          style={{
            margin: 0,
            color: "var(--color-text-muted)",
            fontSize: "var(--text-sm)",
            fontStyle: "italic",
          }}
        >
          next → {verdict.next}
        </p>
      </Stack>
    </Card>
  );
}

function SignalChips({
  signals,
}: {
  signals: NonNullable<ReturnType<typeof synthesizeVerdict>>["signals"];
}) {
  const chips: Array<{ label: string; on: boolean }> = [
    { label: "identity", on: signals.identity },
    { label: "behavior", on: signals.behavior },
    { label: "compromise", on: signals.compromise },
    { label: "consumer", on: signals.consumer_tail },
  ];
  return (
    <span style={{ display: "inline-flex", gap: "var(--space-1)", flexWrap: "wrap" }}>
      {chips.map((c) => (
        <MetaText key={c.label} variant="tag">
          {c.on ? "✓ " : "— "}
          {c.label}
        </MetaText>
      ))}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Section card — Identity / Behavior / Compromise / Property / Visual
// ---------------------------------------------------------------------------

function SectionCard({ section }: { section: Section }) {
  // Dispatch on section.id for sections that need bespoke rendering:
  // - claims_by_entity is a re-indexing primitive, not a data source;
  //   renders with a kicker label + fingerprint mark + chip-row samples.
  // - timeline preserves chronological order and renders cluster rows
  //   as collapsible disclosures with an inline mini-band.
  // - open_web findings are per-engine; each Finding renders as a
  //   labeled mini-section with engine attribution.
  if (section.id === "claims_by_entity") {
    return <ClaimsByEntitySection section={section} />;
  }
  if (section.id === "timeline") {
    return <TimelineSection section={section} />;
  }
  if (section.id === "open_web") {
    return <OpenWebSection section={section} />;
  }

  // Pick the dominant severity to set the card accent.
  const dominantSev: Severity = (() => {
    const sevPriority: Severity[] = ["bad", "warn", "good", "info"];
    for (const s of sevPriority) {
      if (section.findings.some((f) => f.severity === s)) return s;
    }
    return "info";
  })();
  const variant = sevToVariant(dominantSev);
  return (
    <Card padding="md" variant={variant} className="stream-in">
      <Stack gap="3">
        <Stack direction="row" align="baseline" gap="2">
          <MetaText variant="section">{section.title}</MetaText>
          <span
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--color-text-dim)",
            }}
          >
            ({section.findings.length})
          </span>
        </Stack>
        <Stack gap="0">
          {section.findings.map((f, idx) => (
            <FindingRow
              key={`${section.id}-${idx}`}
              finding={f}
              isFirst={idx === 0}
            />
          ))}
        </Stack>
      </Stack>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// claims_by_entity — Iris IA call: navigation primitive, not a data section.
// Distinct rendering (kicker "BY ENTITY", fingerprint icon-mark, compact
// chip-row samples) telegraphs that this is collapsed across all sources,
// not a single adapter's output.
// ---------------------------------------------------------------------------

function ClaimsByEntitySection({ section }: { section: Section }) {
  return (
    <Card padding="md" variant="plain" className="stream-in" style={{
      // H2 (Hideo wave-4 polish-review §Item-1): flat surface-2 fill +
      // single top hairline. The ⌘ fp badge and "By entity" kicker carry
      // the structural signal; gradient + double border was three
      // competing "differentiate this section" moves on one element.
      background: "var(--color-surface-2)",
      borderTop: "1px solid var(--color-border)",
    }}>
      <Stack gap="3">
        <Stack direction="row" align="baseline" gap="2">
          <span
            aria-hidden="true"
            title="entity-fingerprint — collapsed across sections"
            style={{
              fontSize: "var(--text-xs)",
              fontFamily: "var(--font-mono)",
              color: "var(--color-text-dim)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-sm)",
              padding: "1px 5px",
              letterSpacing: "0.05em",
            }}
          >
            ⌘ fp
          </span>
          <MetaText variant="kicker">By entity</MetaText>
          <span
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--color-text-dim)",
            }}
          >
            ({section.findings.length})
          </span>
        </Stack>
        <Stack gap="0">
          {section.findings.map((f, idx) => (
            <EntityFindingRow
              key={`${section.id}-${idx}`}
              finding={f}
              isFirst={idx === 0}
            />
          ))}
        </Stack>
      </Stack>
    </Card>
  );
}

function EntityFindingRow({
  finding,
  isFirst,
}: {
  finding: Finding;
  isFirst: boolean;
}) {
  // Per-row samples are projector-emitted as "source: rawValue" strings.
  // Render them as a compact chip-row so the row reads as
  //   <entity-headline> [≡ canonical] [FIRM]
  //   [src1: value] [src2: value] [src3: value]
  const accentColor =
    finding.severity === "warn"
      ? "var(--color-warn)"
      : finding.severity === "bad"
        ? "var(--color-danger)"
        : "var(--color-text-muted)";
  const distinctSourceCount = finding.samples?.length ?? 0;
  // T1: even by-entity rows carry an asset (LLC/person/etc.) -- grade them.
  const { effective, isOperatorStamped, upgrade } = useFindingConfidence(finding.asset);
  const hasAsset = finding.asset !== undefined && effective !== null;
  const handleKeyDown = hasAsset
    ? (e: React.KeyboardEvent<HTMLDivElement>) => {
        if (e.key === "f" || e.key === "F") {
          e.preventDefault();
          upgrade();
        }
      }
    : undefined;
  const rowProps = hasAsset
    ? {
        tabIndex: 0,
        role: "group" as const,
        "aria-label": `Entity finding: ${finding.headline}. Confidence ${CONFIDENCE_ABBR[effective!]}. Press F to confirm.`,
        onKeyDown: handleKeyDown,
      }
    : {};
  return (
    <div
      className="stream-in"
      {...rowProps}
      style={{
        paddingTop: isFirst ? 0 : "var(--space-3)",
        paddingBottom: "var(--space-3)",
        borderTop: isFirst ? "none" : "1px solid var(--color-border)",
        outline: "none",
      }}
    >
      <Stack gap="2">
        <Stack direction="row" align="baseline" gap="2">
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 4,
              background: accentColor,
              flexShrink: 0,
              marginTop: 6,
            }}
            aria-hidden="true"
          />
          <span
            style={{
              fontSize: "var(--text-base)",
              color: "var(--color-text-primary)",
              fontWeight: 500,
              flex: 1,
              minWidth: 0,
            }}
          >
            {finding.headline}
            {finding.match_type !== undefined ? (
              <CanonicalGlyph matchType={finding.match_type} />
            ) : null}
          </span>
          {distinctSourceCount > 0 ? (
            <MetaText variant="tag">
              {distinctSourceCount} source{distinctSourceCount === 1 ? "" : "s"}
            </MetaText>
          ) : null}
          {hasAsset ? (
            <ConfidenceStamp
              asset={finding.asset!}
              effective={effective!}
              isOperatorStamped={isOperatorStamped}
            />
          ) : null}
          <ProvenancePill provenance={finding.provenance} />
        </Stack>
        {finding.samples && finding.samples.length > 0 ? (
          <div
            style={{
              marginLeft: "calc(var(--space-2) + 8px)",
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--space-1)",
            }}
          >
            {finding.samples.map((s, idx) =>
              s.url ? (
                <a
                  key={idx}
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ textDecoration: "none" }}
                >
                  <MetaText variant="tag" style={{ cursor: "pointer" }}>
                    {s.label}
                  </MetaText>
                </a>
              ) : (
                <MetaText key={idx} variant="tag">
                  {s.label}
                </MetaText>
              ),
            )}
          </div>
        ) : null}
      </Stack>
    </div>
  );
}

// ---------------------------------------------------------------------------
// timeline — chronological projection. Renders cluster Findings (source
// === "timeline-cluster") as collapsed disclosures + inline mini-band.
// ---------------------------------------------------------------------------

function TimelineSection({ section }: { section: Section }) {
  const dominantSev: Severity = (() => {
    const sevPriority: Severity[] = ["bad", "warn", "good", "info"];
    for (const s of sevPriority) {
      if (section.findings.some((f) => f.severity === s)) return s;
    }
    return "info";
  })();
  const variant = sevToVariant(dominantSev);
  return (
    <Card padding="md" variant={variant} className="stream-in">
      <Stack gap="3">
        <Stack direction="row" align="baseline" gap="2">
          <MetaText variant="section">{section.title}</MetaText>
          <span
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--color-text-dim)",
            }}
          >
            ({section.findings.length})
          </span>
        </Stack>
        <Stack gap="0">
          {section.findings.map((f, idx) => {
            if (f.source === "timeline-cluster") {
              return (
                <TimelineClusterRow
                  key={`${section.id}-${idx}`}
                  finding={f}
                  isFirst={idx === 0}
                />
              );
            }
            return (
              <FindingRow
                key={`${section.id}-${idx}`}
                finding={f}
                isFirst={idx === 0}
              />
            );
          })}
        </Stack>
      </Stack>
    </Card>
  );
}

// Parse `YYYY-MM-DD` from the END of a sample label produced by
// `timeline.ts` (samples[].label format: "... — YYYY-MM-DD"). Returns
// null if the trailing token doesn't look like a date.
function trailingIsoDate(label: string): string | null {
  const m = /(\d{4}-\d{2}-\d{2})\s*$/.exec(label);
  return m ? m[1]! : null;
}

function clusterDateRange(
  samples: ReadonlyArray<{ label: string }> | undefined,
): { start: string; end: string } | null {
  if (!samples || samples.length === 0) return null;
  const dates: string[] = [];
  for (const s of samples) {
    const d = trailingIsoDate(s.label);
    if (d !== null) dates.push(d);
  }
  if (dates.length === 0) return null;
  dates.sort();
  return { start: dates[0]!, end: dates[dates.length - 1]! };
}

/**
 * H3 (Hideo wave-4 polish-review §Item-2 "right path"): the modal
 * sub-window is the smallest contiguous date window containing at least
 * ⌈N/3⌉ samples. Used to render true figure-ground in the disclosure:
 * samples inside the window read at opacity 1 (figure); samples outside
 * read at opacity 0.6 (ground). This is the Datadog Watchdog inversion
 * Hideo named as the keystone -- the dense cluster is *visually
 * subtracted* from the noise rather than annotated against it.
 *
 * Returns null when no samples carry a parseable trailing date, OR when
 * fewer than 3 dated samples exist (figure-ground inversion adds no
 * signal at very small N).
 */
function modalSubWindow(
  samples: ReadonlyArray<{ label: string }>,
): { start: string; end: string } | null {
  const dates: string[] = [];
  for (const s of samples) {
    const d = trailingIsoDate(s.label);
    if (d !== null) dates.push(d);
  }
  if (dates.length < 3) return null;
  dates.sort();
  const target = Math.ceil(dates.length / 3);
  // Slide a `target`-wide window across the sorted dates; pick the
  // window with the smallest date-span (most "dense" sub-cluster).
  let bestStart = dates[0]!;
  let bestEnd = dates[target - 1]!;
  let bestSpan = dateDiffDays(bestStart, bestEnd);
  for (let i = 0; i + target <= dates.length; i += 1) {
    const start = dates[i]!;
    const end = dates[i + target - 1]!;
    const span = dateDiffDays(start, end);
    if (span < bestSpan) {
      bestSpan = span;
      bestStart = start;
      bestEnd = end;
    }
  }
  return { start: bestStart, end: bestEnd };
}

function dateDiffDays(a: string, b: string): number {
  // Both are YYYY-MM-DD; Date parsing yields ms-precise epoch.
  const ta = Date.parse(a);
  const tb = Date.parse(b);
  if (Number.isNaN(ta) || Number.isNaN(tb)) return Number.POSITIVE_INFINITY;
  return Math.abs(tb - ta) / 86_400_000;
}

function isDateInWindow(
  date: string,
  window: { start: string; end: string },
): boolean {
  return date >= window.start && date <= window.end;
}

function TimelineClusterRow({
  finding,
  isFirst,
}: {
  finding: Finding;
  isFirst: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const range = clusterDateRange(finding.samples);
  const accentColor =
    finding.severity === "warn"
      ? "var(--color-warn)"
      : finding.severity === "bad"
        ? "var(--color-danger)"
        : "var(--color-text-muted)";
  const sampleCount = finding.samples?.length ?? 0;
  return (
    <div
      className="stream-in"
      style={{
        paddingTop: isFirst ? 0 : "var(--space-3)",
        paddingBottom: "var(--space-3)",
        borderTop: isFirst ? "none" : "1px solid var(--color-border)",
      }}
    >
      <Stack gap="2">
        <Stack direction="row" align="baseline" gap="2">
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 4,
              background: accentColor,
              flexShrink: 0,
              marginTop: 6,
            }}
            aria-hidden="true"
          />
          <span
            style={{
              fontSize: "var(--text-base)",
              color: "var(--color-text-primary)",
              fontWeight: 500,
              flex: 1,
              minWidth: 0,
            }}
          >
            {finding.headline}
          </span>
          <ProvenancePill provenance={finding.provenance} />
        </Stack>
        {/* Mini-band: when the cluster has a parsed date range, render a
         * tinted strip matching event-stream.tsx FigGroundBand (Hideo
         * wave-4 figure-ground signal). Print-color-adjust ensures the
         * band survives the evidence-bundle PDF export. */}
        {range !== null ? (
          <div
            role="img"
            aria-label={`Time window: ${range.start} to ${range.end}`}
            style={{
              marginLeft: "calc(var(--space-2) + 8px)",
              background: "rgba(252, 211, 77, 0.18)",
              border: "1px solid rgba(252, 211, 77, 0.4)",
              borderRadius: "var(--radius-sm)",
              padding: "4px 8px",
              fontSize: "var(--text-xs)",
              fontFamily: "var(--font-mono)",
              color: "var(--color-text-secondary)",
              printColorAdjust: "exact",
              WebkitPrintColorAdjust: "exact",
            }}
          >
            {range.start} → {range.end}
          </div>
        ) : null}
        {finding.detail ? (
          <p
            style={{
              margin: 0,
              marginLeft: "calc(var(--space-2) + 8px)",
              color: "var(--color-text-muted)",
              fontSize: "var(--text-sm)",
            }}
          >
            {finding.detail}
          </p>
        ) : null}
        {sampleCount > 0 ? (
          <div
            style={{
              marginLeft: "calc(var(--space-2) + 8px)",
            }}
          >
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--color-text-dim)",
                fontSize: "var(--text-xs)",
                cursor: "pointer",
                padding: 0,
                fontFamily: "inherit",
                textDecoration: "underline",
                textUnderlineOffset: 2,
              }}
            >
              {expanded ? "▾" : "▸"} {sampleCount} event
              {sampleCount === 1 ? "" : "s"} in cluster
            </button>
            {expanded ? (
              <ul
                style={{
                  margin: "var(--space-2) 0 0 0",
                  padding: 0,
                  listStyle: "none",
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-1)",
                }}
              >
                {finding.samples!.map((s, idx) => (
                  <li
                    key={idx}
                    style={{
                      fontSize: "var(--text-sm)",
                      color: "var(--color-text-secondary)",
                      fontFamily: looksMonoLike(s.label)
                        ? "var(--font-mono)"
                        : "var(--font-ui)",
                    }}
                  >
                    {s.url ? (
                      <a
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ color: "var(--color-text-secondary)" }}
                      >
                        {s.label}
                      </a>
                    ) : (
                      s.label
                    )}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </Stack>
    </div>
  );
}

// ---------------------------------------------------------------------------
// open_web — projector already groups by engine (one Finding per engine).
// Polish: render each Finding as a labeled mini-section with engine kicker.
// ---------------------------------------------------------------------------

function engineFromSource(source: string | undefined): string | null {
  if (!source) return null;
  if (!source.startsWith("dork:")) return null;
  const engine = source.slice("dork:".length).trim();
  return engine || null;
}

function OpenWebSection({ section }: { section: Section }) {
  return (
    <Card padding="md" variant="plain" className="stream-in">
      <Stack gap="3">
        <Stack direction="row" align="baseline" gap="2">
          <MetaText variant="section">{section.title}</MetaText>
          <span
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--color-text-dim)",
            }}
          >
            ({section.findings.length} engine{section.findings.length === 1 ? "" : "s"})
          </span>
        </Stack>
        <Stack gap="3">
          {section.findings.map((f, idx) => {
            const engine = engineFromSource(f.source);
            return (
              <div
                key={`${section.id}-${idx}`}
                className="stream-in"
                style={{
                  paddingTop: idx === 0 ? 0 : "var(--space-3)",
                  borderTop: idx === 0 ? "none" : "1px solid var(--color-border)",
                }}
              >
                <Stack gap="2">
                  {engine ? (
                    <MetaText variant="kicker">
                      {engine.toUpperCase()}
                    </MetaText>
                  ) : null}
                  <FindingRow finding={f} isFirst={true} />
                </Stack>
              </div>
            );
          })}
        </Stack>
      </Stack>
    </Card>
  );
}

function sevToVariant(
  sev: Severity,
): "accent" | "warn" | "danger" | "success" | "plain" {
  switch (sev) {
    case "good":
      return "success";
    case "warn":
      return "warn";
    case "bad":
      return "danger";
    case "info":
      return "plain";
  }
}

// ---------------------------------------------------------------------------
// Finding row — hairline-separated rows inside a section card
// ---------------------------------------------------------------------------

function FindingRow({
  finding,
  isFirst,
}: {
  finding: Finding;
  isFirst: boolean;
}) {
  const accentColor = (() => {
    switch (finding.severity) {
      case "good":
        return "var(--color-success)";
      case "warn":
        return "var(--color-warn)";
      case "bad":
        return "var(--color-danger)";
      default:
        return "var(--color-text-muted)";
    }
  })();
  // T1: when the Finding carries a typed asset, surface a confidence
  // stamp and arm the row for keyboard upgrades (`f` shifts up one tier).
  const { effective, isOperatorStamped, upgrade } = useFindingConfidence(finding.asset);
  const hasAsset = finding.asset !== undefined && effective !== null;
  const handleKeyDown = hasAsset
    ? (e: React.KeyboardEvent<HTMLDivElement>) => {
        if (e.key === "f" || e.key === "F") {
          e.preventDefault();
          upgrade();
        }
      }
    : undefined;
  const rowProps = hasAsset
    ? {
        tabIndex: 0,
        role: "group" as const,
        "aria-label": `Finding: ${finding.headline}. Confidence ${CONFIDENCE_ABBR[effective!]}. Press F to confirm.`,
        onKeyDown: handleKeyDown,
      }
    : {};
  return (
    <div
      className="stream-in"
      {...rowProps}
      style={{
        paddingTop: isFirst ? 0 : "var(--space-3)",
        paddingBottom: "var(--space-3)",
        borderTop: isFirst ? "none" : "1px solid var(--color-border)",
        outline: "none",
      }}
    >
      <Stack gap="2">
        <Stack direction="row" align="baseline" gap="2">
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 4,
              background: accentColor,
              flexShrink: 0,
              marginTop: 6,
            }}
            aria-hidden="true"
          />
          <span
            style={{
              fontSize: "var(--text-base)",
              color: "var(--color-text-primary)",
              fontWeight: 500,
              flex: 1,
              minWidth: 0,
            }}
          >
            {finding.headline}
          </span>
          {hasAsset ? (
            <ConfidenceStamp
              asset={finding.asset!}
              effective={effective!}
              isOperatorStamped={isOperatorStamped}
            />
          ) : null}
          <ProvenancePill provenance={finding.provenance} />
          {finding.source_url ? (
            <a
              href={finding.source_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                fontSize: "var(--text-xs)",
                whiteSpace: "nowrap",
              }}
              title={finding.source_url}
            >
              open ↗
            </a>
          ) : null}
        </Stack>
        {finding.detail ? (
          <p
            style={{
              margin: 0,
              marginLeft: "calc(var(--space-2) + 8px)",
              color: "var(--color-text-muted)",
              fontSize: "var(--text-sm)",
            }}
          >
            {finding.detail}
          </p>
        ) : null}
        {finding.samples && finding.samples.length > 0 ? (
          <ul
            style={{
              margin: 0,
              marginLeft: "calc(var(--space-2) + 8px)",
              padding: 0,
              listStyle: "none",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-1)",
            }}
          >
            {finding.samples.map((s, idx) => (
              <li
                key={idx}
                style={{
                  fontSize: "var(--text-sm)",
                  color: "var(--color-text-secondary)",
                  fontFamily: looksMonoLike(s.label)
                    ? "var(--font-mono)"
                    : "var(--font-ui)",
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                }}
              >
                <span
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    gap: "var(--space-2)",
                    flexWrap: "wrap",
                  }}
                >
                  {s.url ? (
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "var(--color-text-secondary)" }}
                    >
                      {s.label}
                    </a>
                  ) : (
                    s.label
                  )}
                  {s.corroborating_engines && s.corroborating_engines.length > 0 ? (
                    <span
                      title={`Also surfaced by: ${s.corroborating_engines.join(", ")}`}
                      style={{
                        fontSize: "var(--text-xs)",
                        padding: "1px 6px",
                        borderRadius: "var(--radius-xs, 3px)",
                        background: "var(--color-success-soft, rgba(60,180,120,0.15))",
                        color: "var(--color-success, #2a9d6f)",
                        border: "1px solid var(--color-success, #2a9d6f)",
                        fontWeight: 500,
                        whiteSpace: "nowrap",
                      }}
                    >
                      corroborated ×{s.corroborating_engines.length + 1}
                    </span>
                  ) : null}
                </span>
                {s.snippet ? (
                  <span
                    style={{
                      color: "var(--color-text-muted)",
                      fontSize: "var(--text-xs)",
                      fontFamily: "var(--font-ui)",
                      lineHeight: 1.45,
                      paddingLeft: 0,
                    }}
                  >
                    {s.snippet}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        {finding.image_rels && finding.image_rels.length > 0 ? (
          <div
            style={{
              marginLeft: "calc(var(--space-2) + 8px)",
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--space-2)",
              marginTop: "var(--space-1)",
            }}
          >
            {finding.image_rels.map((rel) => (
              <a
                key={rel}
                href={`/api/files/${rel}`}
                target="_blank"
                rel="noopener noreferrer"
                title={rel}
              >
                <img
                  src={`/api/files/${rel}`}
                  alt={rel}
                  loading="lazy"
                  style={{
                    maxHeight: 96,
                    maxWidth: 160,
                    border: "1px solid var(--color-border)",
                    borderRadius: "var(--radius-sm)",
                    background: "var(--color-surface-2)",
                    display: "block",
                  }}
                />
              </a>
            ))}
          </div>
        ) : null}
      </Stack>
    </div>
  );
}

/** Heuristic: looks like an email, URL, or hash -> render in monospace. */
function looksMonoLike(s: string): boolean {
  return /[@./:]|[0-9a-f]{8,}/i.test(s);
}

// ---------------------------------------------------------------------------
// T1 (Tomás polish-use §Item-1): TENTATIVE / FIRM / CONFIRMED confidence
// stamp + one-keystroke upgrade.
//
// Surface: the FindingRow itself. When a Finding carries an `asset`, we
// grade it via assessConfidence() and render a small stamp next to the
// headline. On hover, the tooltip surfaces nextUpgradeHint() so the
// investigator can see how to escalate.
//
// Keyboard: when the row is focused (tabIndex={0}) and the user presses
// `f`, we shift confidence up one tier. The operator-stamped tier is
// stored in component state only -- per Camille's logless doctrine,
// nothing about this annotation outlives the tab close. The
// operator-stamped tier renders with an asterisk (FIRM*) so it's
// distinguishable from auto-graded FIRM.
// ---------------------------------------------------------------------------

const CONFIDENCE_ABBR: Readonly<Record<Confidence, string>> = {
  tentative: "TENTATIVE",
  firm: "FIRM",
  confirmed: "CONFIRMED",
};

const CONFIDENCE_ORDER: ReadonlyArray<Confidence> = ["tentative", "firm", "confirmed"];

function nextTier(current: Confidence): Confidence | null {
  const idx = CONFIDENCE_ORDER.indexOf(current);
  if (idx < 0 || idx === CONFIDENCE_ORDER.length - 1) return null;
  return CONFIDENCE_ORDER[idx + 1] ?? null;
}

function useFindingConfidence(asset: Asset | undefined): {
  effective: Confidence | null;
  isOperatorStamped: boolean;
  upgrade: () => void;
} {
  const [operatorTier, setOperatorTier] = useState<Confidence | null>(null);
  if (asset === undefined) {
    return { effective: null, isOperatorStamped: false, upgrade: () => {} };
  }
  const auto = assessConfidence(asset);
  const effective: Confidence = operatorTier !== null && CONFIDENCE_ORDER.indexOf(operatorTier) > CONFIDENCE_ORDER.indexOf(auto)
    ? operatorTier
    : auto;
  const isOperatorStamped = operatorTier !== null && CONFIDENCE_ORDER.indexOf(operatorTier) > CONFIDENCE_ORDER.indexOf(auto);
  const upgrade = (): void => {
    const next = nextTier(effective);
    if (next !== null) setOperatorTier(next);
  };
  return { effective, isOperatorStamped, upgrade };
}

function ConfidenceStamp({
  asset,
  effective,
  isOperatorStamped,
}: {
  asset: Asset;
  effective: Confidence;
  isOperatorStamped: boolean;
}) {
  const hint = nextUpgradeHint(asset);
  const label = CONFIDENCE_ABBR[effective] + (isOperatorStamped ? "*" : "");
  const title = isOperatorStamped
    ? `${CONFIDENCE_LABEL[effective]} — operator-confirmed this session (asterisk). Not persisted; closes with the tab.`
    : hint !== null
      ? `${CONFIDENCE_LABEL[effective]}. Next step: ${hint}`
      : CONFIDENCE_LABEL[effective];
  return (
    <span
      title={title}
      style={{
        fontSize: "10px",
        fontFamily: "var(--font-mono)",
        color: CONFIDENCE_COLOR[effective],
        border: `1px solid color-mix(in oklch, ${CONFIDENCE_COLOR[effective]} 40%, transparent)`,
        background: "transparent",
        padding: "1px 5px",
        borderRadius: "var(--radius-sm)",
        letterSpacing: "0.06em",
        whiteSpace: "nowrap",
        flexShrink: 0,
        fontWeight: 500,
      }}
    >
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// T2 (Tomás polish-use §Item-2): canonical-id-match glyph for
// entity-fingerprint findings. ≡ means a transform happened (FIRM
// canonical match); ~ means literal-string match (TENTATIVE).
// ---------------------------------------------------------------------------

function CanonicalGlyph({ matchType }: { matchType: NonNullable<Finding["match_type"]> }) {
  const isCanonical = matchType === "canonical_normalized";
  const title = isCanonical
    ? "FIRM canonical match — canonicalize() performed a real transform (suffix stripped, abbrev expanded, or E.164 normalized) and the sources still agreed."
    : "TENTATIVE: identical literal string across sources. No canonical transform was required; the cluster may be a coincidence of identical raw labels.";
  return (
    <span
      aria-hidden="true"
      title={title}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "var(--text-sm)",
        color: isCanonical ? "var(--color-text-secondary)" : "var(--color-text-muted)",
        marginLeft: 4,
        marginRight: 4,
        flexShrink: 0,
      }}
    >
      {isCanonical ? "≡" : "~"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Provenance pill — W4-AI-SCHEMA renderer slot (Camille wave-4
// AI-content defense).
//
// When a Finding carries ai_suspected=true, render a small orange pill
// next to the headline. The Finding itself is NEVER filtered or
// de-ranked (anti-failure-mode is explicit in Provenance docstring at
// dossier-shape.ts:54-59).
//
// When c2pa_present === true, swap to a green "C2PA verified" pill —
// positive provenance signal.
//
// Tooltip surfaces the detector keys so the investigator can see which
// detector(s) flagged this without leaving the dossier surface.
// ---------------------------------------------------------------------------

function ProvenancePill({
  provenance,
}: {
  provenance: Finding["provenance"];
}) {
  if (!provenance) return null;
  // H1 (Hideo wave-4 polish-review §Item-3): pills now resolve through the
  // OKLCH token system (--color-success-soft / --color-warn-soft) so they
  // calibrate against the dark surface, plus color-mix borders so the edge
  // stays tied to the token rather than a hard-sibling sRGB hex. Weight
  // 500 keeps the pill from shouting against an already-saturated palette.
  if (provenance.c2pa_present === true) {
    return (
      <span
        title="C2PA Content Credentials present — provenance attested at capture."
        style={{
          background: "var(--color-success-soft)",
          color: "var(--color-success)",
          border:
            "1px solid color-mix(in oklch, var(--color-success) 30%, transparent)",
          fontSize: "var(--text-xs)",
          fontWeight: 500,
          padding: "2px 6px",
          borderRadius: "var(--radius-sm)",
          letterSpacing: "0.02em",
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
      >
        C2PA verified
      </span>
    );
  }
  if (provenance.ai_suspected === true) {
    const detectors =
      Object.keys(provenance.detector_versions).join(", ") || "(no detectors named)";
    return (
      <span
        title={`AI-suspected — detectors: ${detectors}. Evidence not filtered. See dossier-shape.ts:Provenance.`}
        style={{
          background: "var(--color-warn-soft)",
          color: "var(--color-warn)",
          border:
            "1px solid color-mix(in oklch, var(--color-warn) 30%, transparent)",
          fontSize: "var(--text-xs)",
          fontWeight: 500,
          padding: "2px 6px",
          borderRadius: "var(--radius-sm)",
          letterSpacing: "0.02em",
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
      >
        AI-suspected
      </span>
    );
  }
  return null;
}
