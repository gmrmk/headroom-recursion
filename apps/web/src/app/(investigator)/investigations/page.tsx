import Link from "next/link";

interface InvestigationStub {
  readonly id: string;
  readonly subject: string;
  readonly opened: string;
}

const STUB_LIST: ReadonlyArray<InvestigationStub> = [
  { id: "demo-001", subject: "Username: linustorvalds", opened: "2026-05-10" },
];

export default function InvestigationsPage() {
  return (
    <section>
      <h1 style={{ fontSize: 20, margin: 0, marginBottom: 16 }}>Investigations</h1>
      <p style={{ color: "#a3a3a3", fontSize: 14, maxWidth: 640 }}>
        List + filter of investigations. Real implementation lands in Sprint 2 (WI-0206).
      </p>
      <ul style={{ marginTop: 24, padding: 0, listStyle: "none" }}>
        {STUB_LIST.map((inv) => (
          <li
            key={inv.id}
            style={{
              padding: 12,
              border: "1px solid #1f1f1f",
              borderRadius: 6,
              marginBottom: 8,
            }}
          >
            <Link
              href={`/investigations/${inv.id}`}
              style={{ color: "#e5e5e5", textDecoration: "none" }}
            >
              <strong>{inv.subject}</strong>
              <span style={{ color: "#737373", marginLeft: 12, fontSize: 12 }}>
                {inv.id} · opened {inv.opened}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
