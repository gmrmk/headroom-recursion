import { InvestigationDashboard } from "@/components/investigation-dashboard";

interface PageProps {
  params: Promise<{ id: string }>;
}

export default async function DossierPage({ params }: PageProps) {
  const { id } = await params;
  return (
    <section
      style={{
        maxWidth: 1100,
        marginInline: "auto",
        padding: "var(--space-5) var(--space-5)",
      }}
    >
      <header
        style={{
          marginBottom: "var(--space-5)",
          display: "flex",
          alignItems: "baseline",
          gap: "var(--space-3)",
        }}
      >
        <h2
          style={{
            fontSize: "var(--text-lg)",
            margin: 0,
            color: "var(--color-text-primary)",
            fontWeight: 600,
          }}
        >
          Dossier
        </h2>
        <code
          style={{
            color: "var(--color-text-muted)",
            fontSize: "var(--text-xs)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {id}
        </code>
      </header>

      <InvestigationDashboard investigationId={id} />
    </section>
  );
}
