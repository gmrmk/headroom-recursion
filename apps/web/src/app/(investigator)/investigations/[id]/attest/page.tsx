import type { Metadata } from "next";

interface AttestPageProps {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ reason?: "initial" | "reaffirm" | "expired" }>;
}

export const metadata: Metadata = {
  title: "Lawful Basis Attestation",
};

/**
 * Lawful-basis attestation route — Camille §5.1.1 verbatim baseline.
 * Real implementation lands in WI-0606 (Sprint 1) with bounded reaffirmation chain
 * (Tomas §10.F + Camille INV-S1: ≤20 reaffirmations OR ≤24h, whichever first).
 *
 * URL contract (Iris §10.2-D): /investigations/[id]/attest?reason=initial|reaffirm|expired
 */
export default async function AttestPage({ params, searchParams }: AttestPageProps) {
  const { id } = await params;
  const { reason = "initial" } = await searchParams;

  const reasonText: Record<NonNullable<typeof reason>, string> = {
    initial: "Initial attestation for this investigation",
    reaffirm: "Re-affirming bounded chain (≤20 matches or ≤24h since full attestation)",
    expired: "Previous attestation expired — full retype required",
  };

  return (
    <main
      style={{
        maxWidth: 720,
        margin: "10vh auto",
        padding: 32,
        background: "#0f0f0f",
        border: "1px solid #2a2a2a",
        borderRadius: 8,
        color: "#e5e5e5",
        fontFamily: "ui-monospace, monospace",
        lineHeight: 1.55,
      }}
    >
      <h1 style={{ fontSize: 18, margin: 0, marginBottom: 8 }}>
        Lawful basis attestation required.
      </h1>
      <p style={{ color: "#a3a3a3", fontSize: 13, marginTop: 0 }}>
        Case <code style={{ color: "#e5e5e5" }}>{id}</code> · {reasonText[reason]}
      </p>

      <div style={{ marginTop: 24 }}>
        <p>
          I, <strong>{`{investigator handle}`}</strong>, attest that the biometric processing
          about to occur is necessary for the investigation{" "}
          <strong>{`{case ID}`}</strong> under controller <strong>{`{org name}`}</strong>.
        </p>

        <ul style={{ paddingLeft: 20, color: "#d4d4d4" }}>
          <li>
            Lawful basis (GDPR Art.6):{" "}
            <strong>{`{6(1)(e) public interest / 6(1)(f) legitimate interest}`}</strong>
          </li>
          <li>
            Special category basis (GDPR Art.9):{" "}
            <strong>{`{9(2)(g) substantial public interest / 9(2)(f) legal claims}`}</strong>
          </li>
          <li>
            EU AI Act 2026 Annex III high-risk classification acknowledged: <strong>yes</strong>
          </li>
          <li>
            Retention period for embeddings:{" "}
            <strong>{`{30 days default; per-investigation override}`}</strong>
          </li>
          <li>
            Reaffirmation chain bound: ≤20 matches or ≤24h since last full attestation
          </li>
        </ul>

        <p>
          I have read and understood. <strong>Type &quot;I attest&quot;</strong> to proceed.
        </p>

        <input
          type="text"
          placeholder='Type "I attest" exactly...'
          aria-label="Attestation phrase"
          style={{
            width: "100%",
            padding: 12,
            marginTop: 12,
            background: "#0a0a0a",
            border: "1px solid #2a2a2a",
            borderRadius: 6,
            color: "#e5e5e5",
            fontFamily: "inherit",
            fontSize: 14,
            outline: "none",
            boxSizing: "border-box",
          }}
        />

        <p style={{ color: "#525252", fontSize: 11, marginTop: 24 }}>
          Frozen baseline copy from INTEGRATION-SPEC §8 + Camille §5.1.1. Full signing
          flow + Ed25519 + chain-emit lands in WI-0606 (Sprint 1 verb implementation).
        </p>
      </div>
    </main>
  );
}
