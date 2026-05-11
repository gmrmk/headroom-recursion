// Hand-written API types mirroring apps/api/src/osint_goblin_api/models.py.
// (Will be replaced by `openapi-typescript` generation in Sprint 2 once the
// API exposes a stable schema; for Day 9b we keep this in lockstep manually.)
//
// Source of truth: apps/api/src/osint_goblin_api/models.py. If you change
// one of the Pydantic models, edit this file in the same commit -- the
// pre-commit hook will flag drift in CI once Sprint-2 wires schemathesis.

export type SubjectKind =
  | "username"
  | "email"
  | "phone"
  | "domain"
  | "person"
  | "image"
  | "face"
  | "event";

export interface Subject {
  kind: SubjectKind;
  value: string;
}

export interface Investigation {
  id: string;
  subject: Subject;
  investigator_handle: string;
  notes: string;
  created_at: string;
}

export type InvestigationEventType =
  | "heartbeat"
  | "capture-started"
  | "warc-written"
  | "ed25519-signed"
  | "rfc3161-stamped"
  | "minio-stored"
  | "ftm-entity-created"
  | "wayback-queued"
  | "tool-run-accepted"
  | "tool-run-result"
  | "tool-run-error"
  // R-5 phase6 property-vetting event types (Sprint 2).
  | "geocode-match"
  | "listing-match"
  | "person-match"
  | "breach-hit"
  | "image-match";

export interface InvestigationEvent {
  event_type: InvestigationEventType;
  investigation_id: string;
  run_id: string | null;
  sequence: number;
  ts: string;
  payload: Record<string, unknown>;
}

export type StreamStatus = "idle" | "connecting" | "open" | "closed" | "error";
