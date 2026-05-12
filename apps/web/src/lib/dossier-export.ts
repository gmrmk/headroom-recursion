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
