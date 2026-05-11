"use client";

import { useEffect, useRef, useState } from "react";

import type { InvestigationEvent, StreamStatus } from "@/types/api";

/**
 * Subscribe to `/api/investigations/{invId}/stream` via SSE.
 *
 * Behaviour:
 *   - Opens an EventSource once on mount; re-opens when invId changes.
 *   - Drops dupes by `sequence` (the API guarantees a monotonic seq per
 *     investigation but a reconnect during a soak test can replay the
 *     last event -- dedup by seq is cheap insurance).
 *   - On error, status -> "error" and EventSource auto-retries. We do NOT
 *     manually reconnect: the browser handles backoff per spec.
 *   - Caller decides what to do on "error" (typically a banner; events
 *     received before the error are preserved).
 *
 * Diego sec.B1 + Mei-Lan sec.7 + WI-0206 (Day 9b SSE wiring).
 */
export function useInvestigationStream(invId: string | undefined): {
  events: ReadonlyArray<InvestigationEvent>;
  status: StreamStatus;
} {
  const [events, setEvents] = useState<InvestigationEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const seenSeq = useRef<Set<number>>(new Set());

  useEffect(() => {
    if (!invId) {
      return;
    }
    // Reset state on invId change.
    seenSeq.current = new Set();
    setEvents([]);
    setStatus("connecting");

    const url = `/api/investigations/${invId}/stream`;
    const source = new EventSource(url);

    source.onopen = () => {
      setStatus("open");
    };

    // SSE named events come through addEventListener, not onmessage. The
    // FastAPI side emits one named event per InvestigationEvent.event_type;
    // we register a listener for the generic `message` channel as a fallback
    // and the named events explicitly.
    const handle = (rawData: string) => {
      try {
        const parsed = JSON.parse(rawData) as InvestigationEvent;
        if (seenSeq.current.has(parsed.sequence)) {
          return;
        }
        seenSeq.current.add(parsed.sequence);
        setEvents((prev) => [...prev, parsed]);
      } catch {
        // Malformed event -- skip silently. A future iteration could surface
        // these in the OPSEC HUD; for Day 9b the cost-of-noise is not worth it.
      }
    };

    source.onmessage = (e) => handle(e.data);
    // Register a listener for every InvestigationEventType. The server emits
    // named events; without these listeners only `onmessage` fires for the
    // default channel (which the server does NOT use).
    const eventTypes: ReadonlyArray<InvestigationEvent["event_type"]> = [
      "heartbeat",
      "capture-started",
      "warc-written",
      "ed25519-signed",
      "rfc3161-stamped",
      "minio-stored",
      "ftm-entity-created",
      "wayback-queued",
      "tool-run-accepted",
      "tool-run-result",
      "tool-run-error",
      // R-5 property-vetting event types.
      "geocode-match",
      "listing-match",
      "person-match",
      "breach-hit",
      "image-match",
    ];
    for (const t of eventTypes) {
      source.addEventListener(t, (e) => handle((e as MessageEvent).data));
    }

    source.onerror = () => {
      // The browser auto-retries; we just surface status. CLOSED vs ERROR
      // is informative: CLOSED is "permanent" (4xx, manual close); ERROR is
      // "trying again". The spec says readyState reflects this.
      if (source.readyState === EventSource.CLOSED) {
        setStatus("closed");
      } else {
        setStatus("error");
      }
    };

    return () => {
      source.close();
      setStatus("closed");
    };
  }, [invId]);

  return { events, status };
}
