"use client";

import { useState } from "react";

import {
  collectImageRels,
  downloadDossier,
  fetchImageDataUrls,
  makeDossierFilename,
  serializeDossierHtml,
  serializeDossierMarkdown,
  type DossierContext,
} from "@/lib/dossier-export";
import { synthesizeVerdict } from "@/lib/verdict";
import type { InvestigationEvent } from "@/types/api";

interface DossierExportButtonProps {
  events: ReadonlyArray<InvestigationEvent>;
  context: DossierContext;
}

// Two-button dossier export (markdown + HTML). Both share the same
// verdict synthesis and grouping; only the serializer + extension
// differ. The HTML path additionally inlines image evidence as data:
// URLs so the file is fully self-contained.
//
// Stays within the logless contract (target-data-handling-policy.md):
// no server-side persistence of the dossier; the file the investigator
// saves IS the only record.
export function DossierExportButton({ events, context }: DossierExportButtonProps) {
  const empty = events.length === 0;
  const [htmlExporting, setHtmlExporting] = useState(false);

  function onClickMd() {
    const verdict = synthesizeVerdict(events);
    const md = serializeDossierMarkdown(events, context, verdict);
    downloadDossier(makeDossierFilename(context, "md"), md);
  }

  async function onClickHtml() {
    setHtmlExporting(true);
    try {
      const verdict = synthesizeVerdict(events);
      const rels = collectImageRels(events);
      const dataUrls = await fetchImageDataUrls(rels);
      const html = serializeDossierHtml(events, context, verdict, dataUrls);
      downloadDossier(
        makeDossierFilename(context, "html"),
        html,
        "text/html",
      );
    } finally {
      setHtmlExporting(false);
    }
  }

  const baseStyle: React.CSSProperties = {
    padding: "6px 14px",
    background: empty ? "#1a1a1a" : "#1f1f1f",
    color: empty ? "#525252" : "#e5e5e5",
    border: "1px solid #404040",
    borderRadius: 4,
    fontSize: 12,
    cursor: empty ? "not-allowed" : "pointer",
  };

  return (
    <span style={{ display: "inline-flex", gap: 6 }}>
      <button
        type="button"
        onClick={onClickMd}
        disabled={empty}
        title={
          empty
            ? "No events yet -- run an adapter or workflow first"
            : "Save the investigation as a markdown dossier"
        }
        style={baseStyle}
      >
        .md
      </button>
      <button
        type="button"
        onClick={onClickHtml}
        disabled={empty || htmlExporting}
        title={
          empty
            ? "No events yet -- run an adapter or workflow first"
            : "Save the investigation as a self-contained HTML dossier (inline image evidence)"
        }
        style={{
          ...baseStyle,
          cursor: empty || htmlExporting ? "not-allowed" : "pointer",
        }}
      >
        {htmlExporting ? "fetching images..." : ".html"}
      </button>
    </span>
  );
}
