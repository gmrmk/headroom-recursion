export default function AccountsPage() {
  return (
    <section>
      <h1 style={{ fontSize: 20, margin: 0, marginBottom: 8 }}>Accounts</h1>
      <p style={{ color: "#a3a3a3", fontSize: 14, maxWidth: 640 }}>
        Sock-account ledger. <strong>Top-level peer of Investigations</strong> — not nested under
        Settings (per Iris §1 + Diego ADR-0010 structural-isolation rule).
      </p>
      <p style={{ color: "#737373", fontSize: 13, marginTop: 16 }}>
        Sock accounts live in a separate Postgres database
        (<code style={{ color: "#a3a3a3" }}>osint_sockaccounts</code>) with physical import-path
        split and CI lint rejecting cross-imports. UI lands in WI-0708 (Sprint 7).
      </p>
    </section>
  );
}
