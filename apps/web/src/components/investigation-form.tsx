"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { MetaText } from "@/components/ui/MetaText";
import { Stack } from "@/components/ui/Stack";
import {
  FIELD_META,
  FIELD_ORDER,
  routeWorkflows,
  type InvestigationFields,
} from "@/lib/workflow-routing";

/**
 * InvestigationForm — semantic field surface (Phase 3 of dashboard
 * redesign 2026-05-11).
 *
 * The investigator types into the attributes they're searching, all
 * together in separate fields. The form shows which workflows will
 * dispatch as fields fill (transparent routing, no surprise dispatch),
 * and a single primary `Investigate` button kicks them all off in
 * parallel against the API.
 *
 * Replaces the adapter-dropdown + JSON-payload textarea (RunToolForm)
 * as the default surface. RunToolForm stays reachable behind a "Power
 * user" link for one-off adapter dispatch and dev work.
 */

interface InvestigationFormProps {
  readonly investigationId: string;
}

type SubmitState = "idle" | "submitting" | "ok" | "error";

export function InvestigationForm({ investigationId }: InvestigationFormProps) {
  const [fields, setFields] = useState<InvestigationFields>({});
  const [state, setState] = useState<SubmitState>("idle");
  const [errorMessage, setErrorMessage] = useState<string>("");

  const selections = useMemo(() => routeWorkflows(fields), [fields]);
  const ready = selections.length > 0 && state !== "submitting";

  function setField(key: keyof InvestigationFields, value: string) {
    setFields((prev) => ({ ...prev, [key]: value }));
    if (state !== "idle") {
      setState("idle");
      setErrorMessage("");
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (selections.length === 0) {
      setState("error");
      setErrorMessage("Fill at least one field before investigating.");
      return;
    }
    setState("submitting");
    setErrorMessage("");

    // Dispatch every selected workflow in parallel. Each one is a
    // separate POST /run; the live event stream surfaces all of them
    // interleaved under the same investigation.
    try {
      const responses = await Promise.allSettled(
        selections.map((s) =>
          fetch(`/api/investigations/${investigationId}/run`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ adapter_id: s.id, payload: s.seed }),
          }),
        ),
      );
      const failures = responses
        .map((r, idx) => ({ r, sel: selections[idx]! }))
        .filter(
          ({ r }) =>
            r.status === "rejected" ||
            (r.status === "fulfilled" && !r.value.ok),
        );
      if (failures.length > 0) {
        const first = failures[0]!;
        let reason = "unknown error";
        if (first.r.status === "rejected") {
          reason =
            first.r.reason instanceof Error
              ? first.r.reason.message
              : String(first.r.reason);
        } else if (first.r.status === "fulfilled") {
          const text = await first.r.value.text();
          reason = `HTTP ${first.r.value.status}: ${text.slice(0, 160)}`;
        }
        setState("error");
        setErrorMessage(`${first.sel.id}: ${reason}`);
        return;
      }
      setState("ok");
    } catch (err: unknown) {
      setState("error");
      setErrorMessage(err instanceof Error ? err.message : "network error");
    }
  }

  return (
    <Card padding="lg" style={{ marginBottom: "var(--space-5)" }}>
      <form onSubmit={onSubmit}>
        <Stack gap="4">
          <Stack gap="1">
            <MetaText variant="section">Investigate</MetaText>
            <h2
              style={{
                fontSize: "var(--text-xl)",
                margin: 0,
                color: "var(--color-text-primary)",
                fontWeight: 600,
              }}
            >
              What do you have on the subject?
            </h2>
            <p
              style={{
                margin: 0,
                color: "var(--color-text-muted)",
                fontSize: "var(--text-sm)",
              }}
            >
              Fill any fields you have. We'll run the right workflows in
              parallel and build a single report below.
            </p>
          </Stack>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
              gap: "var(--space-3)",
            }}
          >
            {FIELD_ORDER.map((key) => (
              <FieldRow
                key={key}
                name={key}
                value={fields[key] ?? ""}
                onChange={(v) => setField(key, v)}
              />
            ))}
          </div>

          <WhatThisWillRun selections={selections} />

          <Stack
            direction="row"
            gap="3"
            align="center"
            style={{ marginTop: "var(--space-2)" }}
          >
            <Button
              type="submit"
              variant="primary"
              size="lg"
              disabled={!ready}
              title={
                selections.length === 0
                  ? "Fill at least one field first"
                  : `Run ${selections.length} workflow${selections.length === 1 ? "" : "s"} in parallel`
              }
            >
              {state === "submitting"
                ? "Dispatching…"
                : selections.length === 0
                  ? "Investigate"
                  : `Investigate (${selections.length})`}
            </Button>
            <SubmitFeedback state={state} errorMessage={errorMessage} />
          </Stack>
        </Stack>
      </form>
    </Card>
  );
}

interface FieldRowProps {
  readonly name: keyof InvestigationFields;
  readonly value: string;
  readonly onChange: (v: string) => void;
}

function FieldRow({ name, value, onChange }: FieldRowProps) {
  const meta = FIELD_META[name];
  const isNotes = name === "notes";
  const inputStyle: React.CSSProperties = {
    background: "var(--color-surface-2)",
    color: "var(--color-text-primary)",
    border: "1px solid var(--color-border)",
    borderRadius: "var(--radius-md)",
    padding: "var(--space-2) var(--space-3)",
    fontSize: "var(--text-sm)",
    fontFamily: meta.monospace ? "var(--font-mono)" : "var(--font-ui)",
    outline: "none",
    transition: "border-color var(--duration-fast) var(--ease-out)",
    width: "100%",
    boxSizing: "border-box",
  };
  return (
    <Stack gap="1">
      <label
        htmlFor={`field-${name}`}
        style={{
          fontSize: "var(--text-xs)",
          color: "var(--color-text-secondary)",
          fontWeight: 600,
          letterSpacing: "0.02em",
        }}
      >
        {meta.label}
      </label>
      {isNotes ? (
        <textarea
          id={`field-${name}`}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={meta.placeholder}
          rows={2}
          style={{ ...inputStyle, resize: "vertical", minHeight: 48 }}
        />
      ) : (
        <input
          id={`field-${name}`}
          type={meta.type ?? "text"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={meta.placeholder}
          spellCheck={false}
          autoComplete="off"
          style={inputStyle}
        />
      )}
      {meta.hint ? (
        <span
          style={{
            fontSize: "var(--text-xs)",
            color: "var(--color-text-dim)",
          }}
        >
          {meta.hint}
        </span>
      ) : null}
    </Stack>
  );
}

interface WhatThisWillRunProps {
  readonly selections: ReadonlyArray<{
    readonly id: string;
    readonly label: string;
    readonly why: string;
  }>;
}

function WhatThisWillRun({ selections }: WhatThisWillRunProps) {
  if (selections.length === 0) {
    return (
      <Card padding="md" variant="plain">
        <MetaText variant="kicker">What this will run</MetaText>
        <div
          style={{
            color: "var(--color-text-dim)",
            fontSize: "var(--text-sm)",
            marginTop: "var(--space-1)",
          }}
        >
          Fill any field above. Workflows are picked automatically based on
          which attributes you provide.
        </div>
      </Card>
    );
  }
  return (
    <Card padding="md" variant="accent">
      <Stack gap="2">
        <MetaText variant="kicker">
          What this will run ({selections.length} workflow
          {selections.length === 1 ? "" : "s"} in parallel)
        </MetaText>
        <Stack gap="2" as="ul" style={{ margin: 0, padding: 0, listStyle: "none" }}>
          {selections.map((s) => (
            <Stack
              key={s.id}
              direction="row"
              gap="3"
              align="baseline"
              as="li"
              style={{ minWidth: 0 }}
            >
              <code
                style={{
                  fontSize: "var(--text-xs)",
                  color: "var(--color-accent)",
                  fontFamily: "var(--font-mono)",
                  minWidth: 60,
                }}
              >
                {s.id}
              </code>
              <span
                style={{
                  fontSize: "var(--text-sm)",
                  color: "var(--color-text-primary)",
                  fontWeight: 500,
                  minWidth: 0,
                }}
              >
                {s.label}
              </span>
              <span
                style={{
                  fontSize: "var(--text-xs)",
                  color: "var(--color-text-muted)",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {s.why}
              </span>
            </Stack>
          ))}
        </Stack>
      </Stack>
    </Card>
  );
}

interface SubmitFeedbackProps {
  readonly state: SubmitState;
  readonly errorMessage: string;
}

function SubmitFeedback({ state, errorMessage }: SubmitFeedbackProps) {
  if (state === "ok") {
    return (
      <span
        style={{
          color: "var(--color-success)",
          fontSize: "var(--text-sm)",
        }}
      >
        Dispatched — watch the report fill below.
      </span>
    );
  }
  if (state === "error") {
    return (
      <span
        style={{
          color: "var(--color-danger)",
          fontSize: "var(--text-sm)",
        }}
      >
        {errorMessage || "Error"}
      </span>
    );
  }
  if (state === "submitting") {
    return (
      <span
        style={{
          color: "var(--color-text-muted)",
          fontSize: "var(--text-sm)",
        }}
      >
        Submitting workflows…
      </span>
    );
  }
  return null;
}
