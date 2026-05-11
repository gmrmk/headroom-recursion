/**
 * Centralized keyboard handler — Iris §10.2 + ADR-0007 (six verbs).
 *
 * Sprint-1 scope: cmd-K root palette + cmd-Shift-K scoped + Esc dismiss.
 * Verb-letter bindings (p/c/a/o/e) land in WI-0114 (ADR-0011 commit).
 *
 * All key bindings register here. No component should ever attach a window-level
 * key listener directly — collisions become invisible at PR time.
 */

export type KeyboardScope = "global" | "investigation" | "modal";

export interface KeyBinding {
  readonly id: string;
  readonly key: string; // e.g. "k", "shift+k", "Escape"
  readonly mod: "cmd" | "ctrl" | "cmd-shift" | "ctrl-shift" | "alt" | null;
  readonly scope: KeyboardScope;
  readonly description: string;
}

/**
 * The canonical binding table. WI-0114 will extend this with the verb letters
 * after ADR-0011 lands.
 */
export const KEY_BINDINGS: ReadonlyArray<KeyBinding> = [
  {
    id: "palette.root",
    key: "k",
    mod: "cmd",
    scope: "global",
    description: "Open root command palette",
  },
  {
    id: "palette.scoped",
    key: "k",
    mod: "cmd-shift",
    scope: "investigation",
    description: "Open scoped command palette (current investigation only)",
  },
  {
    id: "modal.dismiss",
    key: "Escape",
    mod: null,
    scope: "modal",
    description: "Dismiss top-most modal / palette",
  },
];

/** Detect cmd (macOS) vs ctrl (Win/Linux) so bindings work cross-platform. */
export function matchesMod(event: KeyboardEvent, mod: KeyBinding["mod"]): boolean {
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  const primary = isMac ? event.metaKey : event.ctrlKey;

  switch (mod) {
    case null:
      return !primary && !event.shiftKey && !event.altKey;
    case "cmd":
    case "ctrl":
      return primary && !event.shiftKey && !event.altKey;
    case "cmd-shift":
    case "ctrl-shift":
      return primary && event.shiftKey && !event.altKey;
    case "alt":
      return event.altKey && !primary && !event.shiftKey;
    default:
      return false;
  }
}

export function matchesBinding(event: KeyboardEvent, binding: KeyBinding): boolean {
  return (
    event.key.toLowerCase() === binding.key.toLowerCase() && matchesMod(event, binding.mod)
  );
}
