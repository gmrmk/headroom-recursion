"use client";

import { useEffect, useMemo, useState } from "react";

import type { AdapterGroup, AdapterSelectDetail } from "@/lib/adapters-catalog";
import {
  ADAPTERS,
  ADAPTER_BY_ID,
  ADAPTER_SELECT_EVENT,
  GROUP_LABELS,
  GROUP_ORDER,
} from "@/lib/adapters-catalog";

interface RunToolFormProps {
  investigationId: string;
}


export function RunToolForm({ investigationId }: RunToolFormProps) {
  const [adapterId, setAdapterId] = useState<string>(ADAPTERS[0]!.id);
  const [payloadText, setPayloadText] = useState<string>(ADAPTERS[0]!.examplePayload);
  const [status, setStatus] = useState<"idle" | "submitting" | "ok" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string>("");

  const selectedAdapter = ADAPTERS.find((a) => a.id === adapterId) ?? ADAPTERS[0]!;

  // Partition once per render-pass; preserves Iris-IA's optgroup order.
  // Each adapter's group field is the source of truth (lib/adapters-catalog).
  const grouped = useMemo(() => {
    type Bucket = Array<(typeof ADAPTERS)[number]>;
    const buckets: Record<AdapterGroup, Bucket> = {
      workflow: [],
      addrgeo: [],
      email: [],
      phone: [],
      domain: [],
      person: [],
      social: [],
      image: [],
      smoke: [],
    };
    for (const a of ADAPTERS) {
      buckets[a.group].push(a);
    }
    return buckets;
  }, []);

  function selectAdapter(next: string) {
    setAdapterId(next);
    const adapter = ADAPTER_BY_ID[next];
    if (adapter) {
      setPayloadText(adapter.examplePayload);
    }
    setStatus("idle");
    setErrorMessage("");
  }

  // Palette wire: command-palette dispatches ADAPTER_SELECT_EVENT on the
  // window when the user picks an adapter via cmd-K. The form listens and
  // updates its adapter selection in place (preserves the payload editor
  // so investigators can review + tweak before Run).
  useEffect(() => {
    function handle(event: Event) {
      const detail = (event as CustomEvent<AdapterSelectDetail>).detail;
      if (detail?.adapterId && ADAPTER_BY_ID[detail.adapterId]) {
        selectAdapter(detail.adapterId);
      }
    }
    window.addEventListener(ADAPTER_SELECT_EVENT, handle);
    return () => window.removeEventListener(ADAPTER_SELECT_EVENT, handle);
    // selectAdapter closes over setters which are stable; deps intentionally empty.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          {GROUP_ORDER.map((g) =>
            grouped[g].length === 0 ? null : (
              <optgroup key={g} label={GROUP_LABELS[g]}>
                {grouped[g].map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.label}
                  </option>
                ))}
              </optgroup>
            ),
          )}
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
