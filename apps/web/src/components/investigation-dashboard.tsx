"use client";

import { useState } from "react";

import { EventStream } from "@/components/event-stream";
import { InvestigationForm } from "@/components/investigation-form";
import { InvestigationReport } from "@/components/investigation-report";
import { RunToolForm } from "@/components/run-tool-form";
import { Card } from "@/components/ui/Card";
import { MetaText } from "@/components/ui/MetaText";
import { Stack } from "@/components/ui/Stack";
import { useInvestigationStream } from "@/hooks/useInvestigationStream";

/**
 * Dashboard wrapper — owns the SSE subscription and renders the new
 * investigator surface (form + live one-glance report) by default.
 *
 * "Power user" toggle reveals the previous surface (adapter dropdown +
 * JSON payload editor + full raw event stream) for one-off adapter
 * dispatch and debugging. Nothing deleted; cutover-by-default.
 */

interface InvestigationDashboardProps {
  readonly investigationId: string;
}

export function InvestigationDashboard({
  investigationId,
}: InvestigationDashboardProps) {
  const { events, status } = useInvestigationStream(investigationId);
  const [powerUser, setPowerUser] = useState(false);

  return (
    <Stack gap="5">
      <InvestigationForm investigationId={investigationId} />
      <InvestigationReport events={events} investigationId={investigationId} />

      <Stack
        direction="row"
        align="center"
        justify="between"
        gap="2"
        style={{ marginTop: "var(--space-3)" }}
      >
        <span
          style={{
            fontSize: "var(--text-xs)",
            color: "var(--color-text-faint)",
            fontFamily: "var(--font-mono)",
          }}
        >
          stream {status}
        </span>
        <button
          type="button"
          onClick={() => setPowerUser((v) => !v)}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--color-text-dim)",
            fontSize: "var(--text-xs)",
            cursor: "pointer",
            padding: "var(--space-1) var(--space-2)",
            textDecoration: "underline",
            textUnderlineOffset: 2,
          }}
        >
          {powerUser ? "Hide power-user surface" : "Power user (adapter + raw events)"}
        </button>
      </Stack>

      {powerUser ? (
        <Card padding="md" variant="plain">
          <Stack gap="4">
            <Stack gap="1">
              <MetaText variant="kicker">Power user</MetaText>
              <p
                style={{
                  margin: 0,
                  fontSize: "var(--text-sm)",
                  color: "var(--color-text-muted)",
                }}
              >
                One-off adapter dispatch + raw event stream. The default
                investigation surface above runs whole workflows; this is for
                running a single adapter against a custom payload, or for
                seeing every event verbatim during a debug session.
              </p>
            </Stack>
            <RunToolForm investigationId={investigationId} />
            <EventStream investigationId={investigationId} />
          </Stack>
        </Card>
      ) : null}
    </Stack>
  );
}
