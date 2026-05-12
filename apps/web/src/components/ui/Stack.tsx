import type { CSSProperties, ReactNode } from "react";

/**
 * Stack — flex layout primitive. Replaces the dozens of
 *   style={{ display: "flex", flexDirection: "column", gap: 8 }}
 * inline blocks scattered across the components.
 *
 * direction: "row" | "column"
 * gap: token-keyed; "1" through "8" map to --space-N
 * align: cross-axis alignment
 * wrap: flex-wrap (off by default)
 */

type Direction = "row" | "column";
type Gap = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8";
type Align = "stretch" | "start" | "center" | "end" | "baseline";
type Justify = "start" | "center" | "end" | "between" | "around";

const ALIGN_MAP: Record<Align, CSSProperties["alignItems"]> = {
  stretch: "stretch",
  start: "flex-start",
  center: "center",
  end: "flex-end",
  baseline: "baseline",
};

const JUSTIFY_MAP: Record<Justify, CSSProperties["justifyContent"]> = {
  start: "flex-start",
  center: "center",
  end: "flex-end",
  between: "space-between",
  around: "space-around",
};

interface StackProps {
  children: ReactNode;
  direction?: Direction;
  gap?: Gap;
  align?: Align;
  justify?: Justify;
  wrap?: boolean;
  style?: CSSProperties;
  as?: "div" | "section" | "header" | "footer" | "nav" | "ul" | "li";
}

export function Stack({
  children,
  direction = "column",
  gap = "3",
  align = "stretch",
  justify = "start",
  wrap = false,
  style,
  as: Component = "div",
}: StackProps) {
  const baseStyle: CSSProperties = {
    display: "flex",
    flexDirection: direction,
    gap: `var(--space-${gap})`,
    alignItems: ALIGN_MAP[align],
    justifyContent: JUSTIFY_MAP[justify],
    flexWrap: wrap ? "wrap" : "nowrap",
  };
  return <Component style={{ ...baseStyle, ...style }}>{children}</Component>;
}
