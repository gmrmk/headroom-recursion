import type { ButtonHTMLAttributes, CSSProperties } from "react";

/**
 * Button — variants:
 *   "primary"   accent-filled; one per important surface (e.g. Investigate)
 *   "secondary" surface-2 background, used for export, run, save
 *   "ghost"     transparent; used for cancel / toggle / "Power user" links
 *
 * Sizes:
 *   "sm" 28px tall, compact   (status bar buttons, .md/.html exports)
 *   "md" 36px tall, default   (form submits)
 *   "lg" 44px tall, hero      (Investigate)
 */

type ButtonVariant = "primary" | "secondary" | "ghost";
type ButtonSize = "sm" | "md" | "lg";

const HEIGHT: Record<ButtonSize, number> = { sm: 28, md: 36, lg: 44 };
const PADDING_X: Record<ButtonSize, string> = {
  sm: "var(--space-3)",
  md: "var(--space-4)",
  lg: "var(--space-5)",
};
const TEXT_SIZE: Record<ButtonSize, string> = {
  sm: "var(--text-xs)",
  md: "var(--text-sm)",
  lg: "var(--text-base)",
};

function variantStyles(variant: ButtonVariant, disabled: boolean): CSSProperties {
  if (disabled) {
    return {
      background: "var(--color-surface)",
      color: "var(--color-text-dim)",
      border: "1px solid var(--color-border)",
      cursor: "not-allowed",
    };
  }
  switch (variant) {
    case "primary":
      return {
        background: "var(--color-accent)",
        color: "oklch(15% 0.005 240)",
        border: "1px solid var(--color-accent)",
        cursor: "pointer",
        fontWeight: 600,
      };
    case "secondary":
      return {
        background: "var(--color-surface-2)",
        color: "var(--color-text-primary)",
        border: "1px solid var(--color-border-strong)",
        cursor: "pointer",
      };
    case "ghost":
      return {
        background: "transparent",
        color: "var(--color-text-secondary)",
        border: "1px solid transparent",
        cursor: "pointer",
      };
  }
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
};

export function Button({
  variant = "secondary",
  size = "md",
  style,
  disabled = false,
  children,
  ...rest
}: ButtonProps) {
  const vstyle = variantStyles(variant, disabled);
  const baseStyle: CSSProperties = {
    height: HEIGHT[size],
    padding: `0 ${PADDING_X[size]}`,
    fontSize: TEXT_SIZE[size],
    fontFamily: "var(--font-ui)",
    borderRadius: "var(--radius-md)",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "var(--space-2)",
    transition:
      "background var(--duration-fast) var(--ease-out), border-color var(--duration-fast) var(--ease-out), transform var(--duration-fast) var(--ease-out)",
    ...vstyle,
  };
  return (
    <button {...rest} disabled={disabled} style={{ ...baseStyle, ...style }}>
      {children}
    </button>
  );
}
