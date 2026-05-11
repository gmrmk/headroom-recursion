import { EventStream } from "@/components/event-stream";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default async function DossierPage({ params }: PageProps) {
  const { id } = await params;
  return (
    <section>
      <header style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, margin: 0 }}>Dossier</h2>
        <p style={{ color: "#737373", fontSize: 13, margin: "4px 0 0 0" }}>
          Case <code style={{ color: "#a3a3a3" }}>{id}</code>
        </p>
      </header>

      <EventStream investigationId={id} />
    </section>
  );
}
