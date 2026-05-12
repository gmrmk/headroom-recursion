import type { CSSProperties, ReactNode } from "react";

/**
 * MetaText — small uppercase tracking-wide label. The "VERDICT" /
 * "FINDINGS" / "SOURCE" labels that anchor each section of the report.
 *
 * variants:
 *   "section"  small caps, neutral; for section headers ("VERDICT")
 *   "kicker"   one-line dim above a heading
 *   "tag"      inline-rendered category tag inside a Card
 */

type MetaVariant = "section" | "kicker" | "tag";

const VARIANT_STYLES: Record<MetaVariant, CSSProperties> = {
  section: {
    fontSize: "var(--text-xs)",
    color: "var(--color-text-muted)",
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    fontWeight: 600,
  },
  kicker: {
    fontSize: "var(--text-xs)",
    color: "var(--color-text-dim)",
    letterSpacing: "0.04em",
    textTransform: "uppercase",
  },
  tag: {
    fontSize: "var(--text-xs)",
    color: "var(--color-text-secondary)",
    background: "var(--color-surface-2)",
    border: "1px solid var(--color-border)",
    borderRadius: "var(--radius-sm)",
    padding: "2px 6px",
    display: "inline-block",
    letterSpacing: "0.02em",
  },
};

interface MetaTextProps {
  children: ReactNode;
  variant?: MetaVariant;
  style?: CSSProperties;
}

export function MetaText({ children, variant = "section", style }: MetaTextProps) {
  const Component = variant === "tag" ? "span" : "div";
  return (
    <Component style={{ ...VARIANT_STYLES[variant], ...style }}>
      {children}
    </Component>
  );
}
