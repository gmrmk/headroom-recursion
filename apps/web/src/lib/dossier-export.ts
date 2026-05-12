// Client-side dossier export -- markdown serializer.
//
// Turns the investigation's live event stream + Margaret's verdict into
// a single markdown document the investigator can save, share via Obsidian,
// commit to a private notes vault, or simply keep as evidence.
//
// Stays within the logless contract (target-data-handling-policy.md):
// nothing persists server-side. The dossier IS the only allowed
// persistence and it lives on the investigator's own disk.
//
// Sections:
//   1. Header (investigation id + subject + timestamp)
//   2. Verdict (bucket + confidence + why + next + signal chips)
//   3. Findings grouped by event_type with per-source rollups
//   4. Errors (if any)
//   5. Audit-trail footer (export timestamp + event count + commit hint)

import type { Verdict } from "./verdict";
import type { InvestigationEvent } from "@/types/api";

export interface DossierContext {
  readonly investigationId: string;
  readonly subjectKind?: string;
  readonly subjectValue?: string;
  readonly investigatorHandle?: string;
  readonly createdAt?: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function escapeMd(s: string): string {
  // Markdown-escape only the characters that break our layout. Investigator
  // values aren't trusted to be markdown-safe (could contain `|` `<` etc.).
  return s.replace(/[<>`|]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "`": "\\`", "|": "\\|" }[c] ?? c));
}

function summarizePayload(payload: Record<string, unknown>, maxLen = 200): string {
  const json = JSON.stringify(payload);
  if (json.length <= maxLen) {
    return json;
  }
  return json.slice(0, maxLen - 3) + "...";
}

// Group events by event_type, then by payload.source within each type.
// Returns ordered groups so the dossier sections always render in the
// same order regardless of arrival order.
interface SourceGroup {
  readonly source: string;
  readonly events: InvestigationEvent[];
}

interface EventTypeGroup {
  readonly eventType: string;
  readonly sources: SourceGroup[];
  readonly totalCount: number;
}

function groupEvents(events: ReadonlyArray<InvestigationEvent>): EventTypeGroup[] {
  // Event-type ordering for dossier readability: identity-positive first,
  // compromise/error last, structural last.
  const TYPE_ORDER = [
    "person-match",
    "breach-hit",
    "image-match",
    "geocode-match",
    "listing-match",
    "tool-run-result",
    "tool-run-error",
    "adapter-failure",
  ];
  const byType = new Map<string, Map<string, InvestigationEvent[]>>();
  for (const e of events) {
    if (e.event_type === "heartbeat" || e.event_type === "tool-run-accepted") {
      continue;
    }
    const src =
      typeof e.payload?.source === "string" ? (e.payload.source as string) : "(unsourced)";
    const typeBucket = byType.get(e.event_type) ?? new Map<string, InvestigationEvent[]>();
    const srcBucket = typeBucket.get(src) ?? [];
    srcBucket.push(e);
    typeBucket.set(src, srcBucket);
    byType.set(e.event_type, typeBucket);
  }
  const out: EventTypeGroup[] = [];
  const seenTypes = new Set<string>();
  for (const type of TYPE_ORDER) {
    const sources = byType.get(type);
    if (!sources) continue;
    seenTypes.add(type);
    const sourceGroups: SourceGroup[] = [];
    let total = 0;
    for (const [source, evs] of sources.entries()) {
      sourceGroups.push({ source, events: evs });
      total += evs.length;
    }
    sourceGroups.sort((a, b) => b.events.length - a.events.length);
    out.push({ eventType: type, sources: sourceGroups, totalCount: total });
  }
  // Any remaining types not in TYPE_ORDER, appended in insertion order.
  for (const [type, sources] of byType.entries()) {
    if (seenTypes.has(type)) continue;
    const sourceGroups: SourceGroup[] = [];
    let total = 0;
    for (const [source, evs] of sources.entries()) {
      sourceGroups.push({ source, events: evs });
      total += evs.length;
    }
    out.push({ eventType: type, sources: sourceGroups, totalCount: total });
  }
  return out;
}

export function serializeDossierMarkdown(
  events: ReadonlyArray<InvestigationEvent>,
  ctx: DossierContext,
  verdict: Verdict | null,
): string {
  const lines: string[] = [];
  const subject = ctx.subjectValue
    ? `${ctx.subjectKind ?? "?"}: ${ctx.subjectValue}`
    : ctx.investigationId;
  lines.push(`# Investigation Dossier — ${escapeMd(subject)}`);
  lines.push("");
  lines.push(`- **Investigation ID:** \`${ctx.investigationId}\``);
  if (ctx.investigatorHandle) {
    lines.push(`- **Investigator:** ${escapeMd(ctx.investigatorHandle)}`);
  }
  if (ctx.createdAt) {
    lines.push(`- **Created:** ${ctx.createdAt}`);
  }
  lines.push(`- **Exported:** ${nowIso()}`);
  lines.push(`- **Event count:** ${events.length}`);
  lines.push("");

  // Verdict block. If verdict is null (no events of interest yet),
  // include a marker so the export is self-describing.
  lines.push("## Verdict");
  lines.push("");
  if (verdict) {
    lines.push(`**${verdict.bucket}** (${verdict.confidence} confidence)`);
    lines.push("");
    lines.push(`> ${escapeMd(verdict.why)}`);
    lines.push("");
    lines.push(`**Next:** ${escapeMd(verdict.next)}`);
    lines.push("");
    lines.push("### Signals");
    lines.push("");
    const chip = (label: string, on: boolean) =>
      `- ${on ? "✓" : "—"} **${label}**`;
    lines.push(chip("identity", verdict.signals.identity));
    lines.push(chip("behavior", verdict.signals.behavior));
    lines.push(chip("compromise", verdict.signals.compromise));
    lines.push(chip("consumer tail", verdict.signals.consumer_tail));
  } else {
    lines.push("*No verdict yet — investigation has insufficient signal to classify.*");
  }
  lines.push("");

  // Findings, grouped by event_type then by source.
  const groups = groupEvents(events);
  lines.push("## Findings");
  lines.push("");
  if (groups.length === 0) {
    lines.push("*No findings to report.*");
    lines.push("");
  } else {
    for (const grp of groups) {
      lines.push(`### ${grp.eventType} (${grp.totalCount})`);
      lines.push("");
      for (const src of grp.sources) {
        lines.push(`#### source: \`${escapeMd(src.source)}\` (${src.events.length})`);
        lines.push("");
        for (const e of src.events) {
          lines.push(`- \`#${e.sequence}\` ${e.ts} — ${escapeMd(summarizePayload(e.payload))}`);
        }
        lines.push("");
      }
    }
  }

  lines.push("---");
  lines.push("");
  lines.push(
    `Generated by osint-goblin at ${nowIso()}. ` +
      `Logless: this dossier file IS the only persistence; ` +
      `nothing about this investigation lives outside it.`,
  );
  lines.push("");
  return lines.join("\n");
}

export function downloadDossier(
  filename: string,
  content: string,
  mimeType = "text/markdown",
): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  // Programmatic click works in all modern browsers. We avoid
  // appendChild + removeChild dance since the anchor isn't rendered.
  a.click();
  URL.revokeObjectURL(url);
}

export function makeDossierFilename(
  ctx: DossierContext,
  ext = "md",
): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const tag =
    ctx.subjectValue
      ? ctx.subjectValue.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 32)
      : ctx.investigationId.slice(0, 12);
  return `dossier-${tag}-${stamp}.${ext}`;
}


// ---------------------------------------------------------------------------
// HTML dossier (v2) -- visual evidence preservation
//
// Self-contained HTML with inline <style> + inline data: URLs for any
// image artifact referenced by a payload's *_rel key. Investigator can
// open in any browser without the API running; nothing about the file
// depends on the server side.
//
// The Blob export pattern is shared with markdown -- only the
// serializer + filename extension change.
// ---------------------------------------------------------------------------


// Bucket -> color hint. Mirrors apps/web/src/lib/verdict.ts BUCKET_COLOR
// but kept local to the serializer so the HTML is self-contained.
const HTML_BUCKET_COLOR: Record<string, string> = {
  "compromised-real": "#fbbf24",
  "real-careful": "#34d399",
  "real-active": "#34d399",
  "suspicious-churn": "#f87171",
  "low-footprint": "#fbbf24",
  mixed: "#60a5fa",
};

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Collect unique *_rel paths from the event stream. These are paths
// relative to data/ that the API serves at /api/files/<rel>. The caller
// pre-resolves each to a data: URL before calling serializeDossierHtml.
export function collectImageRels(
  events: ReadonlyArray<InvestigationEvent>,
): string[] {
  const rels = new Set<string>();
  for (const e of events) {
    if (typeof e.payload !== "object" || e.payload === null) continue;
    for (const [k, v] of Object.entries(e.payload)) {
      if (k.endsWith("_rel") && typeof v === "string" && v) {
        rels.add(v);
      }
    }
  }
  return Array.from(rels);
}

const HTML_STYLE = `
  :root {
    color-scheme: dark light;
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0a0a0a;
    color: #e5e5e5;
    margin: 0;
    padding: 32px;
    max-width: 1024px;
    margin-inline: auto;
    line-height: 1.5;
  }
  h1 { font-size: 22px; margin: 0 0 8px 0; }
  h2 { font-size: 16px; margin: 24px 0 8px 0; color: #a3a3a3; letter-spacing: 0.5px; text-transform: uppercase; }
  h3 { font-size: 14px; margin: 16px 0 6px 0; color: #d4d4d4; }
  h4 { font-size: 13px; margin: 8px 0 4px 0; color: #737373; font-weight: 600; }
  .meta { color: #737373; font-size: 13px; margin: 0 0 16px 0; }
  .meta code { background: #1a1a1a; padding: 1px 5px; border-radius: 3px; color: #d4d4d4; }
  .verdict {
    padding: 12px 16px;
    border-radius: 4px;
    background: #0f0f0f;
    border: 1px solid #1f1f1f;
    border-left-width: 4px;
    margin: 16px 0;
  }
  .verdict-bucket { font-size: 18px; font-weight: 600; }
  .verdict-confidence { color: #737373; margin-left: 8px; font-size: 12px; }
  .verdict-why { color: #a3a3a3; margin: 6px 0; }
  .verdict-next { color: #737373; font-style: italic; font-size: 13px; }
  .signal-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  .chip {
    padding: 2px 8px; border: 1px solid #2a2a2a; border-radius: 3px; font-size: 11px;
  }
  .chip.on { background: #1f1f1f; color: #e5e5e5; }
  .chip.off { color: #525252; }
  .group {
    background: #0f0f0f;
    border: 1px solid #1f1f1f;
    border-radius: 4px;
    padding: 12px 16px;
    margin: 8px 0;
  }
  ul.events { list-style: none; padding: 0; margin: 4px 0 0 0; }
  ul.events > li {
    font-family: ui-monospace, SFMono-Regular, monospace;
    font-size: 12px;
    color: #a3a3a3;
    padding: 4px 0;
    border-top: 1px solid #1f1f1f;
  }
  ul.events > li:first-child { border-top: none; }
  .seq { color: #525252; }
  .ts { color: #525252; }
  .evidence-grid {
    display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px;
  }
  .evidence-grid img {
    max-width: 200px; max-height: 200px;
    border: 1px solid #2a2a2a; border-radius: 4px; background: #1a1a1a;
  }
  .footer { margin-top: 32px; color: #525252; font-size: 11px; text-align: center; border-top: 1px solid #1f1f1f; padding-top: 16px; }
  .empty { color: #525252; font-style: italic; }
`;

interface SourceGroupHtml {
  readonly source: string;
  readonly events: InvestigationEvent[];
}

interface EventTypeGroupHtml {
  readonly eventType: string;
  readonly sources: SourceGroupHtml[];
  readonly totalCount: number;
}

// Same grouping logic as the markdown serializer. Inlined to keep the
// two serializers independent (markdown could evolve separately).
function groupEventsHtml(
  events: ReadonlyArray<InvestigationEvent>,
): EventTypeGroupHtml[] {
  const TYPE_ORDER = [
    "person-match",
    "breach-hit",
    "image-match",
    "geocode-match",
    "listing-match",
    "tool-run-result",
    "tool-run-error",
    "adapter-failure",
  ];
  const byType = new Map<string, Map<string, InvestigationEvent[]>>();
  for (const e of events) {
    if (e.event_type === "heartbeat" || e.event_type === "tool-run-accepted") {
      continue;
    }
    const src =
      typeof e.payload?.source === "string"
        ? (e.payload.source as string)
        : "(unsourced)";
    const typeBucket = byType.get(e.event_type) ?? new Map<string, InvestigationEvent[]>();
    const srcBucket = typeBucket.get(src) ?? [];
    srcBucket.push(e);
    typeBucket.set(src, srcBucket);
    byType.set(e.event_type, typeBucket);
  }
  const out: EventTypeGroupHtml[] = [];
  const seenTypes = new Set<string>();
  for (const type of TYPE_ORDER) {
    const sources = byType.get(type);
    if (!sources) continue;
    seenTypes.add(type);
    const sg: SourceGroupHtml[] = [];
    let total = 0;
    for (const [source, evs] of sources.entries()) {
      sg.push({ source, events: evs });
      total += evs.length;
    }
    sg.sort((a, b) => b.events.length - a.events.length);
    out.push({ eventType: type, sources: sg, totalCount: total });
  }
  for (const [type, sources] of byType.entries()) {
    if (seenTypes.has(type)) continue;
    const sg: SourceGroupHtml[] = [];
    let total = 0;
    for (const [source, evs] of sources.entries()) {
      sg.push({ source, events: evs });
      total += evs.length;
    }
    out.push({ eventType: type, sources: sg, totalCount: total });
  }
  return out;
}

function renderEventInlineImages(
  e: InvestigationEvent,
  dataUrls: ReadonlyMap<string, string>,
): string {
  if (typeof e.payload !== "object" || e.payload === null) return "";
  const imgs: string[] = [];
  for (const [k, v] of Object.entries(e.payload)) {
    if (!k.endsWith("_rel") || typeof v !== "string" || !v) continue;
    const dataUrl = dataUrls.get(v);
    if (!dataUrl) continue;
    const label = k.replace(/_rel$/, "");
    imgs.push(
      `<img src="${escapeHtml(dataUrl)}" alt="${escapeHtml(label)}" title="${escapeHtml(label)}" loading="lazy" />`,
    );
  }
  if (imgs.length === 0) return "";
  return `<div class="evidence-grid">${imgs.join("")}</div>`;
}

export function serializeDossierHtml(
  events: ReadonlyArray<InvestigationEvent>,
  ctx: DossierContext,
  verdict: Verdict | null,
  imageDataUrls: ReadonlyMap<string, string> = new Map(),
): string {
  const subject = ctx.subjectValue
    ? `${ctx.subjectKind ?? "?"}: ${ctx.subjectValue}`
    : ctx.investigationId;

  const verdictHtml = verdict
    ? (() => {
        const color = HTML_BUCKET_COLOR[verdict.bucket] ?? "#60a5fa";
        const chip = (label: string, on: boolean) =>
          `<span class="chip ${on ? "on" : "off"}">${on ? "✓" : "—"} ${label}</span>`;
        return `<div class="verdict" style="border-left-color: ${color}">
          <div><span class="verdict-bucket" style="color: ${color}">${escapeHtml(verdict.bucket)}</span><span class="verdict-confidence">${escapeHtml(verdict.confidence)} confidence</span></div>
          <div class="verdict-why">${escapeHtml(verdict.why)}</div>
          <div class="verdict-next">next → ${escapeHtml(verdict.next)}</div>
          <div class="signal-chips">
            ${chip("identity", verdict.signals.identity)}
            ${chip("behavior", verdict.signals.behavior)}
            ${chip("compromise", verdict.signals.compromise)}
            ${chip("consumer tail", verdict.signals.consumer_tail)}
          </div>
        </div>`;
      })()
    : `<div class="verdict"><p class="empty">No verdict yet — investigation has insufficient signal to classify.</p></div>`;

  const groups = groupEventsHtml(events);
  const findingsHtml =
    groups.length === 0
      ? `<p class="empty">No findings to report.</p>`
      : groups
          .map((grp) => {
            const sources = grp.sources
              .map((src) => {
                const items = src.events
                  .map((e) => {
                    const payloadJson = JSON.stringify(e.payload);
                    const truncated =
                      payloadJson.length > 200
                        ? payloadJson.slice(0, 197) + "..."
                        : payloadJson;
                    const imgs = renderEventInlineImages(e, imageDataUrls);
                    return `<li>
                      <span class="seq">#${e.sequence}</span>
                      <span class="ts">${escapeHtml(e.ts)}</span>
                      — ${escapeHtml(truncated)}
                      ${imgs}
                    </li>`;
                  })
                  .join("");
                return `<div class="group">
                  <h4>source: <code>${escapeHtml(src.source)}</code> (${src.events.length})</h4>
                  <ul class="events">${items}</ul>
                </div>`;
              })
              .join("");
            return `<section>
              <h3>${escapeHtml(grp.eventType)} (${grp.totalCount})</h3>
              ${sources}
            </section>`;
          })
          .join("");

  const exportedAt = new Date().toISOString();
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Investigation Dossier — ${escapeHtml(subject)}</title>
  <style>${HTML_STYLE}</style>
</head>
<body>
  <h1>Investigation Dossier — ${escapeHtml(subject)}</h1>
  <p class="meta">
    <strong>Investigation ID:</strong> <code>${escapeHtml(ctx.investigationId)}</code><br />
    ${ctx.investigatorHandle ? `<strong>Investigator:</strong> ${escapeHtml(ctx.investigatorHandle)}<br />` : ""}
    ${ctx.createdAt ? `<strong>Created:</strong> ${escapeHtml(ctx.createdAt)}<br />` : ""}
    <strong>Exported:</strong> ${escapeHtml(exportedAt)}<br />
    <strong>Event count:</strong> ${events.length}<br />
    <strong>Inline images:</strong> ${imageDataUrls.size}
  </p>

  <h2>Verdict</h2>
  ${verdictHtml}

  <h2>Findings</h2>
  ${findingsHtml}

  <div class="footer">
    Generated by osint-goblin at ${escapeHtml(exportedAt)}.
    Logless contract: this file IS the only persistence; nothing about
    this investigation lives outside it.
  </div>
</body>
</html>`;
}

// Browser-only: fetch image rels via /api/files/<rel>, encode each as a
// data: URL so the exported HTML is self-contained. Falls back to no
// image when a fetch fails -- the dossier still renders.
export async function fetchImageDataUrls(
  rels: ReadonlyArray<string>,
): Promise<Map<string, string>> {
  const out = new Map<string, string>();
  await Promise.all(
    rels.map(async (rel) => {
      try {
        const r = await fetch(`/api/files/${rel}`);
        if (!r.ok) return;
        const blob = await r.blob();
        const dataUrl = await new Promise<string | null>((resolve) => {
          const reader = new FileReader();
          reader.onloadend = () => resolve(reader.result as string);
          reader.onerror = () => resolve(null);
          reader.readAsDataURL(blob);
        });
        if (dataUrl) out.set(rel, dataUrl);
      } catch {
        // Network error -- skip. The dossier still renders without
        // this particular image.
      }
    }),
  );
  return out;
}
