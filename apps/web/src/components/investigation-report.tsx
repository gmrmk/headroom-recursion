"use client";

import { useMemo } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { MetaText } from "@/components/ui/MetaText";
import { Stack } from "@/components/ui/Stack";
import {
  collectImageRels,
  downloadDossier,
  fetchImageDataUrls,
  makeDossierFilename,
  serializeDossierHtml,
  serializeDossierMarkdown,
} from "@/lib/dossier-export";
import {
  buildReportShape,
  type Finding,
  type Section,
  type Severity,
} from "@/lib/dossier-shape";
import { synthesizeVerdict } from "@/lib/verdict";
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
  investigationId,
}: InvestigationReportProps) {
  const shape = useMemo(() => buildReportShape(events), [events]);
  const verdict = useMemo(() => synthesizeVerdict(events), [events]);

  const ctx = { investigationId };

  function onSaveMd() {
    const md = serializeDossierMarkdown(events, ctx, verdict);
    downloadDossier(makeDossierFilename(ctx, "md"), md);
  }

  async function onSaveHtml() {
    const rels = collectImageRels(events);
    const dataUrls = await fetchImageDataUrls(rels);
    const html = serializeDossierHtml(events, ctx, verdict, dataUrls);
    downloadDossier(makeDossierFilename(ctx, "html"), html, "text/html");
  }

  const visibleSections = shape.sections.filter((s) => s.findings.length > 0);

  return (
    <Stack gap="4">
      {/* Top bar: section meta + export buttons */}
      <Stack direction="row" align="center" justify="between" gap="3">
        <Stack gap="0">
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
        <Stack direction="row" gap="2" align="center">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={onSaveMd}
            disabled={shape.event_count === 0}
            title="Save the investigation as a markdown dossier"
          >
            .md
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={onSaveHtml}
            disabled={shape.event_count === 0}
            title="Save the investigation as a self-contained HTML dossier"
          >
            .html
          </Button>
        </Stack>
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
