import type { CSSProperties, ReactNode } from "react";

/**
 * Card — surface primitive used wherever we group findings.
 *
 * variant controls border emphasis:
 *   "plain"   default; subtle border, default surface
 *   "accent"  border + left-rail in the accent color (verdict, primary section)
 *   "warn"    warning accent
 *   "danger"  danger accent
 *   "success" success accent
 *
 * padding controls inner space:
 *   "sm" --space-3  (compact)
 *   "md" --space-4  (default)
 *   "lg" --space-5  (hero / verdict)
 */

type CardVariant = "plain" | "accent" | "warn" | "danger" | "success";
type CardPadding = "sm" | "md" | "lg";

const PADDING_TOKEN: Record<CardPadding, string> = {
  sm: "var(--space-3)",
  md: "var(--space-4)",
  lg: "var(--space-5)",
};

const ACCENT_TOKEN: Record<Exclude<CardVariant, "plain">, string> = {
  accent: "var(--color-accent)",
  warn: "var(--color-warn)",
  danger: "var(--color-danger)",
  success: "var(--color-success)",
};

interface CardProps {
  children: ReactNode;
  variant?: CardVariant;
  padding?: CardPadding;
  /** Optional inline-style escape hatch for one-off overrides. */
  style?: CSSProperties;
}

export function Card({
  children,
  variant = "plain",
  padding = "md",
  style,
}: CardProps) {
  const baseStyle: CSSProperties = {
    background: "var(--color-surface)",
    border: "1px solid var(--color-border)",
    borderRadius: "var(--radius-md)",
    padding: PADDING_TOKEN[padding],
  };
  if (variant !== "plain") {
    const accent = ACCENT_TOKEN[variant];
    baseStyle.borderColor = `oklch(from ${accent} l c h / 0.35)`;
    baseStyle.borderLeftWidth = 3;
    baseStyle.borderLeftColor = accent;
  }
  return <div style={{ ...baseStyle, ...style }}>{children}</div>;
}
