interface PageProps {
  params: Promise<{ id: string }>;
}

export default async function ExportPage({ params }: PageProps) {
  const { id } = await params;
  return (
    <section>
      <h2 style={{ fontSize: 16, margin: 0, marginBottom: 8 }}>Export</h2>
      <p style={{ color: "#737373", fontSize: 13 }}>
        Case <code style={{ color: "#a3a3a3" }}>{id}</code> · Evidence-package zip (WARC + sigs + manifest + verify.py)
      </p>
      <p style={{ color: "#525252", fontSize: 12, marginTop: 16 }}>
        Real implementation lands in WI-0202 + WI-0208 (Sprint 1 exit gate).
      </p>
    </section>
  );
}
