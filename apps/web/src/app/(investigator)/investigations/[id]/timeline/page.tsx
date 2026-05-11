interface PageProps {
  params: Promise<{ id: string }>;
}

export default async function TimelinePage({ params }: PageProps) {
  const { id } = await params;
  return (
    <section>
      <h2 style={{ fontSize: 16, margin: 0, marginBottom: 8 }}>Timeline</h2>
      <p style={{ color: "#737373", fontSize: 13 }}>
        Case <code style={{ color: "#a3a3a3" }}>{id}</code> · Event timeline of captures + pivots
      </p>
      <p style={{ color: "#525252", fontSize: 12, marginTop: 16 }}>
        Real implementation lands in WI-0206 (Sprint 2).
      </p>
    </section>
  );
}
