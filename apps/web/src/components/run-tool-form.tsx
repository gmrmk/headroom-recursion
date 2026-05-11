"use client";

import { useState } from "react";

interface RunToolFormProps {
  investigationId: string;
}

// The eight property-vetting + smoke adapters wired in apps/workers.
// Names mirror osint_goblin_workers.adapters + adapters_property.
// Kept inline (not fetched) so the form renders synchronously; a future
// /adapters endpoint can replace this when the registry surface lands.
const ADAPTERS: ReadonlyArray<{
  readonly id: string;
  readonly label: string;
  readonly hint: string;
  readonly examplePayload: string;
}> = [
  {
    id: "nominatim_geocode",
    label: "Nominatim — address → lat/lon",
    hint: "OSM geocoding. 1 req/sec self-throttled.",
    examplePayload: '{\n  "q": "1600 Pennsylvania Ave, Washington DC"\n}',
  },
  {
    id: "email_mx_validate",
    label: "Email — DNS MX validate",
    hint: "Format + DNS-A record probe. Catches typos.",
    examplePayload: '{\n  "email": "user@example.com"\n}',
  },
  {
    id: "hibp_breach_check",
    label: "HIBP — breach by domain",
    hint: "Have I Been Pwned free 'breaches-by-domain' endpoint.",
    examplePayload: '{\n  "email": "user@example.com"\n}',
  },
  {
    id: "inside_airbnb_listings",
    label: "Inside Airbnb — host fingerprint",
    hint: "Search pre-downloaded city CSV; flags commercial operators.",
    examplePayload:
      '{\n  "csv_path": "data/inside-airbnb/city.csv",\n  "host_name": "Alice"\n}',
  },
  {
    id: "true_people_search",
    label: "TruePeopleSearch — person",
    hint: "Scrapling stealth subprocess. Top 5 results, single page.",
    examplePayload: '{\n  "name": "Alice Smith",\n  "city": "Springfield",\n  "state": "IL"\n}',
  },
  {
    id: "tineye_image",
    label: "TinEye — reverse image",
    hint: "URL-based reverse-image search via Scrapling.",
    examplePayload: '{\n  "image_url": "https://example.com/photo.jpg"\n}',
  },
  {
    id: "echo",
    label: "echo — smoke",
    hint: "Trivial in-process adapter. Returns payload as tool-run-result.",
    examplePayload: '{\n  "hello": "world"\n}',
  },
  {
    id: "m0_gate_stress",
    label: "m0_gate_stress — 32-event burst",
    hint: "M0 exit gate path. In-process; bypasses Dramatiq.",
    examplePayload: "{}",
  },
];

export function RunToolForm({ investigationId }: RunToolFormProps) {
  const [adapterId, setAdapterId] = useState<string>(ADAPTERS[0]!.id);
  const [payloadText, setPayloadText] = useState<string>(ADAPTERS[0]!.examplePayload);
  const [status, setStatus] = useState<"idle" | "submitting" | "ok" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string>("");

  const selectedAdapter = ADAPTERS.find((a) => a.id === adapterId) ?? ADAPTERS[0]!;

  function selectAdapter(next: string) {
    setAdapterId(next);
    const adapter = ADAPTERS.find((a) => a.id === next);
    if (adapter) {
      setPayloadText(adapter.examplePayload);
    }
    setStatus("idle");
    setErrorMessage("");
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    let parsedPayload: unknown;
    try {
      parsedPayload = payloadText.trim() === "" ? {} : JSON.parse(payloadText);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "invalid JSON";
      setStatus("error");
      setErrorMessage(`Payload JSON parse: ${msg}`);
      return;
    }
    setStatus("submitting");
    setErrorMessage("");
    try {
      const res = await fetch(`/api/investigations/${investigationId}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ adapter_id: adapterId, payload: parsedPayload }),
      });
      if (!res.ok) {
        const text = await res.text();
        setStatus("error");
        setErrorMessage(`HTTP ${res.status}: ${text.slice(0, 200)}`);
        return;
      }
      setStatus("ok");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "network error";
      setStatus("error");
      setErrorMessage(msg);
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      style={{
        marginBottom: 16,
        padding: 12,
        background: "#0f0f0f",
        border: "1px solid #1f1f1f",
        borderRadius: 4,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <label
          htmlFor="adapter-select"
          style={{ color: "#a3a3a3", fontSize: 12, minWidth: 60 }}
        >
          Adapter
        </label>
        <select
          id="adapter-select"
          value={adapterId}
          onChange={(e) => selectAdapter(e.target.value)}
          style={{
            flex: 1,
            background: "#1a1a1a",
            color: "#e5e5e5",
            border: "1px solid #2a2a2a",
            borderRadius: 4,
            padding: "4px 6px",
            fontSize: 12,
          }}
        >
          {ADAPTERS.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
            </option>
          ))}
        </select>
      </div>
      <p style={{ color: "#525252", fontSize: 11, margin: 0 }}>{selectedAdapter.hint}</p>
      <textarea
        value={payloadText}
        onChange={(e) => setPayloadText(e.target.value)}
        aria-label="JSON payload"
        spellCheck={false}
        rows={6}
        style={{
          background: "#1a1a1a",
          color: "#d4d4d4",
          border: "1px solid #2a2a2a",
          borderRadius: 4,
          padding: 8,
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          fontSize: 12,
          resize: "vertical",
        }}
      />
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button
          type="submit"
          disabled={status === "submitting"}
          style={{
            padding: "6px 14px",
            background: status === "submitting" ? "#1a1a1a" : "#1f1f1f",
            color: status === "submitting" ? "#525252" : "#e5e5e5",
            border: "1px solid #404040",
            borderRadius: 4,
            fontSize: 12,
            cursor: status === "submitting" ? "not-allowed" : "pointer",
          }}
        >
          {status === "submitting" ? "Submitting..." : "Run"}
        </button>
        <span style={{ color: statusColor(status), fontSize: 12 }}>{statusLabel(status)}</span>
        {errorMessage ? (
          <span style={{ color: "#f87171", fontSize: 11 }}>{errorMessage}</span>
        ) : null}
      </div>
    </form>
  );
}

function statusLabel(s: "idle" | "submitting" | "ok" | "error"): string {
  switch (s) {
    case "idle":
      return "";
    case "submitting":
      return "submitting...";
    case "ok":
      return "enqueued — watch the stream below";
    case "error":
      return "error";
  }
}

function statusColor(s: "idle" | "submitting" | "ok" | "error"): string {
  switch (s) {
    case "idle":
      return "#525252";
    case "submitting":
      return "#f59e0b";
    case "ok":
      return "#34d399";
    case "error":
      return "#f87171";
  }
}
