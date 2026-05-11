import type { ReactNode } from "react";

interface CaseLayoutProps {
  children: ReactNode;
  params: Promise<{ id: string }>;
}

const TABS: ReadonlyArray<{ readonly slug: string; readonly label: string }> = [
  { slug: "", label: "Dossier" }, // default — / route
  { slug: "graph", label: "Graph" },
  { slug: "evidence", label: "Evidence" },
  { slug: "timeline", label: "Timeline" },
  { slug: "export", label: "Export" },
];

export default async function CaseLayout({ children, params }: CaseLayoutProps) {
  const { id } = await params;
  return (
    <div>
      {/* Iris §1 — facet rail above dossier; placeholder for now */}
      <div
        style={{
          padding: "8px 12px",
          background: "#141414",
          border: "1px solid #1f1f1f",
          borderRadius: 6,
          marginBottom: 16,
          color: "#737373",
          fontSize: 12,
        }}
      >
        Facet rail (Aleph-inspired) — Dataset · Dates · Entity type · Country · Email · Phone · Name · Address · Configure filters
      </div>

      {/* Tabs row */}
      <nav
        style={{
          display: "flex",
          gap: 4,
          borderBottom: "1px solid #1f1f1f",
          marginBottom: 16,
        }}
      >
        {TABS.map((tab) => {
          const href = tab.slug ? `/investigations/${id}/${tab.slug}` : `/investigations/${id}`;
          return (
            <a
              key={tab.slug || "dossier"}
              href={href}
              style={{
                padding: "8px 14px",
                color: "#d4d4d4",
                textDecoration: "none",
                fontSize: 13,
                borderBottom: "2px solid transparent",
              }}
            >
              {tab.label}
            </a>
          );
        })}
      </nav>

      <div>{children}</div>
    </div>
  );
}
