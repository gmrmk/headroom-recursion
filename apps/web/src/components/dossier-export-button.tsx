"use client";

import {
  downloadDossier,
  makeDossierFilename,
  serializeDossierMarkdown,
  type DossierContext,
} from "@/lib/dossier-export";
import { synthesizeVerdict } from "@/lib/verdict";
import type { InvestigationEvent } from "@/types/api";

interface DossierExportButtonProps {
  events: ReadonlyArray<InvestigationEvent>;
  context: DossierContext;
}

// One-click markdown export of the investigation. Computes the verdict
// from the same rubric the live banner uses, serializes events grouped
// by type + source, triggers a Blob download. Stays within the logless
// contract (target-data-handling-policy.md): no server-side persistence
// of the dossier; the file the investigator saves IS the only record.
export function DossierExportButton({ events, context }: DossierExportButtonProps) {
  const empty = events.length === 0;
  function onClick() {
    const verdict = synthesizeVerdict(events);
    const md = serializeDossierMarkdown(events, context, verdict);
    downloadDossier(makeDossierFilename(context, "md"), md);
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={empty}
      title={
        empty
          ? "No events yet -- run an adapter or workflow first"
          : "Save the investigation as a markdown dossier"
      }
      style={{
        padding: "6px 14px",
        background: empty ? "#1a1a1a" : "#1f1f1f",
        color: empty ? "#525252" : "#e5e5e5",
        border: "1px solid #404040",
        borderRadius: 4,
        fontSize: 12,
        cursor: empty ? "not-allowed" : "pointer",
      }}
    >
      Save dossier (.md)
    </button>
  );
}
